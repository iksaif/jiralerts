#!/usr/bin/env python3

import os
import sys

import base64
import click
from flask import Flask, request, make_response
from jira import JIRA
import jinja2
import prometheus_client as prometheus

app = Flask(__name__)

jira = None


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def prepareGroupKey(gk):
    return base64.b64encode(gk.encode())


JINJA_ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT_DIR))
JINJA_ENV.filters['prepareGroupKey'] = prepareGroupKey

summary_tmpl = JINJA_ENV.get_template('templates/summary.tmpl')
description_tmpl = JINJA_ENV.get_template('templates/description.tmpl')
description_boundary = '_-- Alertmanager -- [only edit above]_'

# Order for the search query is important for the query performance. It relies
# on the 'alert_group_key' field in the description that must not be modified.
search_query = 'project = %s and ' + \
               'labels = "alert" and ' + \
               'status not in (%s) and ' + \
               'description ~ "alert_group_key=%s"'

jira_request_time = prometheus.Histogram('jira_request_latency_seconds',
                                         'Latency when querying the JIRA API',
                                         ['action'])
request_time = prometheus.Histogram('request_latency_seconds',
                                    'Latency of incoming requests')

jira_request_time_transitions = jira_request_time.labels({'action': 'transitions'})
jira_request_time_close = jira_request_time.labels({'action': 'close'})
jira_request_time_update = jira_request_time.labels({'action': 'update'})
jira_request_time_create = jira_request_time.labels({'action': 'create'})


@jira_request_time_transitions.time()
def transitions(issue):
    return jira.transitions(issue)


@jira_request_time_close.time()
def close(issue, tid):
    return jira.transition_issue(issue, tid)


@jira_request_time_update.time()
def update_issue(issue, summary, description):
    custom_desc = issue.fields.description.rsplit(description_boundary, 1)[0]
    return issue.update(
        summary=summary,
        description="%s\n\n%s\n%s" % (custom_desc.strip(), description_boundary, description))


@jira_request_time_create.time()
def create_issue(project, issue_type, summary, description):
    return jira.create_issue({
        'project': {'key': project},
        'summary': summary,
        'description': "%s\n\n%s" % (description_boundary, description),
        'issuetype': {'name': issue_type},
        'labels': ['alert', ],
    })


@app.route('/-/health')
def health():
    return "OK", 200


@request_time.time()
@app.route('/issues/<project>/<issue_type>', methods=['POST'])
def file_issue(project, issue_type):
    """
    This endpoint accepts a JSON encoded notification according to the version 3 or 4
    of the generic webhook of the Prometheus Alertmanager.
    """
    data = request.get_json()
    if data['version'] not in ["3", "4"]:
        return "unknown message version %s" % data['version'], 400

    resolved = data['status'] == "resolved"
    description = description_tmpl.render(data)
    summary = summary_tmpl.render(data)

    # If there's already a ticket for the incident, update it and close if necessary.
    result = jira.search_issues(search_query % (
        project, ','.join(resolved_status), prepareGroupKey(data['groupKey'])))
    if result:
        issue = result[0]

        # Try different possible transitions for resolved incidents
        # in order of preference. Different ones may work for different boards.
        if resolved:
            valid_trans = [
                t for t in transitions(issue) if t['name'].lower() in resolve_transitions]
            if valid_trans:
                close(issue, valid_trans[0]['id'])
            else:
                print("Unable to find transition to close %s" % issue)

        # Update the base information regardless of the transition.
        update_issue(issue, summary, description)

    # Do not create an issue for resolved incidents that were never filed.
    elif not resolved:
        create_issue(project, issue_type, summary, description)

    return "", 200


@app.route('/metrics')
def metrics():
    resp = make_response(prometheus.generate_latest(prometheus.core.REGISTRY))
    resp.headers['Content-Type'] = prometheus.CONTENT_TYPE_LATEST
    return resp, 200


@click.command()
@click.option('--host', help='Host listen address')
@click.option('--port', '-p', default=9050, help='Listen port for the webhook')
@click.option('--res_transitions', default="resolve issue,close issue",
              help='Comma separated list of known transitions used to resolve alerts')
@click.option('--res_status', default="resolved,closed,fixed,done,complete",
              help='Comma separated list of known resolved status')
@click.option('--debug', '-d', default=False, is_flag=True, help='Enable debug mode')
@click.argument('server')
def main(host, port, server, res_transitions, res_status, debug):
    global jira

    global resolve_transitions, resolved_status
    resolve_transitions = res_transitions.split(',')
    resolved_status = res_status.split(',')

    username = os.environ.get('JIRA_USERNAME')
    password = os.environ.get('JIRA_PASSWORD')
    if not username or not password:
        print("JIRA_USERNAME or JIRA_PASSWORD not set")
        sys.exit(2)

    jira = JIRA(basic_auth=(username, password), server=server, logging=debug)
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
