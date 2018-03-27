#!/usr/bin/env python3

import base64
import os
import sys
import logging

import hashlib
import click
import jinja2
import prometheus_client as prometheus
import flask
from jira import JIRA

try:
    from raven.contrib.flask import Sentry
except ImportError:
    Sentry = None

app = flask.Flask(__name__)

jira = None

LOG_FORMAT = (
    '[%(asctime)s] %(levelname)s %(module)s '
    '[%(filename)s:%(funcName)s:%(lineno)d] (%(thread)d): %(message)s')
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


class Error(Exception):
    """All local errors."""
    pass


def prepare_group_key(gk):
    """Create a unique key for an alert group."""
    return base64.b64encode(gk.encode()).decode()


def prepare_group_label_key(gk):
    """Create a unique key by hashing an alert group."""
    hash_label = hashlib.sha1(gk.encode()).hexdigest()
    return hash_label[0:10]


def prepare_tags(common_labels):
    """Get JIRA tags from alert labels."""
    tags_whitelist = ['severity', 'dc', 'env', 'perimeter', 'team', 'jiralert']
    tags = ['alert', ]
    for k, v in common_labels.items():
        if k in tags_whitelist:
            tags.append('%s:%s' % (k, v))
        if k == 'tags':
            tags.extend([tag.strip() for tag in v.split(',') if tag])
    return tags


JINJA_ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT_DIR))
JINJA_ENV.filters['prepareGroupKey'] = prepare_group_key

summary_tmpl = JINJA_ENV.get_template('templates/summary.tmpl')
description_tmpl = JINJA_ENV.get_template('templates/description.tmpl')
DESCRIPTION_BOUNDARY = '_-- Alertmanager -- [only edit above]_'

# Order for the search query is important for the query performance. It relies
# on the 'alert_group_key' field in the description that must not be modified.
SEARCH_QUERY = 'project = "%s" and ' + \
               'issuetype = "%s" and ' + \
               'labels = "alert" and ' + \
               'status not in (%s) and ' + \
               '(description ~ "alert_group_key=%s" or ' + \
               'labels = "jiralert:%s")'

jira_request_time = prometheus.Histogram('jira_request_latency_seconds',
                                         'Latency when querying the JIRA API',
                                         ['action'])
jira_request_time_transitions = jira_request_time.labels(action='transitions')
jira_request_time_close = jira_request_time.labels(action='close')
jira_request_time_update = jira_request_time.labels(action='update')
jira_request_time_create = jira_request_time.labels(action='create')

request_time = prometheus.Histogram('request_latency_seconds',
                                    'Latency of incoming requests',
                                    ['endpoint'])
request_time_generic_issues = request_time.labels(endpoint='/issues')
request_time_qualified_issues = request_time.labels(
    endpoint='/issues/<project>/<issue_type>')


@jira_request_time_transitions.time()
def transitions(issue):
    return jira.transitions(issue)


@jira_request_time_close.time()
def close(issue, tid):
    return jira.transition_issue(issue, tid)


@jira_request_time_update.time()
def update_issue(issue, summary, description, tags):
    custom_desc = issue.fields.description.rsplit(DESCRIPTION_BOUNDARY, 1)[0]

    # Merge expected tags and existing ones
    fields = {"labels": list(set(issue.fields.labels + tags))}

    return issue.update(
        summary=summary,
        fields=fields,
        description="%s\n\n%s\n%s" % (custom_desc.strip(), DESCRIPTION_BOUNDARY, description))


@jira_request_time_create.time()
def create_issue(project, issue_type, summary, description, tags):
    return jira.create_issue({
        'project': {'key': project},
        'summary': summary,
        'description': "%s\n\n%s" % (DESCRIPTION_BOUNDARY, description),
        'issuetype': {'name': issue_type},
        'labels': tags,
    })


@app.route('/')
def index():
    return 'jiralert. <a href="/metrics">metrics</a>'


@app.route('/favicon.ico')
def favicon():
    return flask.send_from_directory(
        ROOT_DIR,
        'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    )


@app.route('/-/health')
def health():
    return "OK", 200


@request_time_generic_issues.time()
@app.route('/issues', methods=['POST'])
def parse_issue_params():
    """
    This endpoint accepts a JSON encoded notification according to the version 3 or 4
    of the generic webhook of the Prometheus Alertmanager.
    """
    data = flask.request.get_json()
    if data['version'] not in ["3", "4"]:
        return "unknown message version %s" % data['version'], 400

    common_labels = data['commonLabels']
    if 'issue_type' not in common_labels or 'project' not in common_labels:
        return "Required commonLabels not found: issue_type or project", 400

    issue_type = common_labels['issue_type']
    project = common_labels['project']
    return file_issue(project=project, issue_type=issue_type)


@request_time_qualified_issues.time()
@app.route('/issues/<project>/<issue_type>', methods=['POST'])
def file_issue(project, issue_type):
    """
    This endpoint accepts a JSON encoded notification according to the version 3 or 4
    of the generic webhook of the Prometheus Alertmanager.
    """
    app.logger.info("issue: %s %s" % (project, issue_type))

    data = flask.request.get_json()
    if data['version'] not in ["3", "4"]:
        return "unknown message version %s" % data['version'], 400

    resolved = data['status'] == "resolved"
    tags = prepare_tags(data['commonLabels'])
    tags.append('jiralert:%s' % prepare_group_label_key(data['groupKey']))

    description = description_tmpl.render(data)
    summary = summary_tmpl.render(data)

    # If there's already a ticket for the incident, update it and close if necessary.
    result = jira.search_issues(SEARCH_QUERY % (
        project, issue_type, ','.join(resolved_status),
        prepare_group_key(data['groupKey']),
        prepare_group_label_key(data['groupKey'])))
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
                app.logger.warning(
                    "Unable to find transition to close %s" % issue)

        # Update the base information regardless of the transition.
        update_issue(issue, summary, description, tags)

    # Do not create an issue for resolved incidents that were never filed.
    elif not resolved:
        create_issue(project, issue_type, summary, description, tags)

    return "", 200


@app.route('/metrics')
def metrics():
    resp = flask.make_response(
        prometheus.generate_latest(prometheus.core.REGISTRY))
    resp.headers['Content-Type'] = prometheus.CONTENT_TYPE_LATEST
    return resp, 200


def setup_app(server, res_transitions, res_status, debug, loglevel):
    # TODO: get rid of globals. Maybe store that inside app. itself.
    global jira
    global resolve_transitions, resolved_status

    resolve_transitions = res_transitions.split(',')
    resolved_status = res_status.split(',')

    username = os.environ.get('JIRA_USERNAME')
    password = os.environ.get('JIRA_PASSWORD')
    if not username or not password:
        print("JIRA_USERNAME or JIRA_PASSWORD not set")
        sys.exit(2)

    if loglevel:
        # Remove existing logger.
        app.config['LOGGER_HANDLER_POLICY'] = 'never'
        app.logger.propagate = True

        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.getLevelName(loglevel))
        app.logger.info("Logging initialized.")

    dsn = os.getenv('SENTRY_DSN', None)
    if dsn and Sentry:
        sentry = Sentry(dsn=dsn)
        sentry.init_app(app)
        app.logger.info("Sentry is enabled.")

    app.logger.info("Connecting to JIRA.""")
    jira = JIRA(basic_auth=(username, password), server=server, logging=True)
    return app


def run_with_werkzeug(host, port, debug, app, threads):
    """Run with werkzeug simple wsgi container."""
    threaded = threads is not None and (threads > 0)
    app.run(host=host, port=port, debug=debug, threaded=threaded)


def run_with_twisted(host, port, debug, app, threads, loglevel):
    """Run with twisted."""
    from twisted.internet import reactor
    from twisted.python import log
    import flask_twisted

    twisted = flask_twisted.Twisted(app)
    if threads:
        reactor.suggestThreadPoolSize(threads)
    if loglevel:
        log.startLogging(sys.stderr)
    twisted.run(host=host, port=port, debug=debug)


@click.command()
@click.option('--host', help='Host listen address')
@click.option('--port', '-p', default=9050, help='Listen port for the webhook', type=int)
@click.option('--res_transitions', default="resolve issue,close issue",
              help='Comma separated list of known transitions used to resolve alerts')
@click.option('--res_status', default="resolved,closed,done,complete",
              help='Comma separated list of known resolved status')
@click.option('--debug', '-d', default=False, is_flag=True, help='Enable debug mode')
@click.option('--loglevel', '-l', default='INFO', help='Log Level, empty string to disable.')
@click.option('--twisted', default=False, is_flag=True, help='Use twisted to server requests.')
@click.option('--threads', default=None, help='Number of threads to use.', type=int)
@click.argument('server')
def main(host, port, server, res_transitions, res_status, debug,
         loglevel, twisted, threads):
    setup_app(server, res_transitions, res_status, debug, loglevel)
    if not twisted:
        run_with_werkzeug(host, port, debug, app, threads)
    else:
        run_with_twisted(host, port, debug, app, threads, loglevel)


if __name__ == "__main__":
    main()
