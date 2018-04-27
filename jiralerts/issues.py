#!/usr/bin/env python3

import hashlib
import jinja2
import os
import logging
import collections
import time

import prometheus_client as prometheus
from jira import JIRA, JIRAError


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


errors = prometheus.Counter("errors_total", "Number of errors")
jira_errors = prometheus.Counter(
    "jira_errors_total", "Number of jira errors", ["action"]
)
jira_errors_transitions = jira_errors.labels(action="transitions")
jira_errors_close = jira_errors.labels(action="close")
jira_errors_update = jira_errors.labels(action="update")
jira_errors_create = jira_errors.labels(action="create")

jira_request_time = prometheus.Histogram(
    "jira_request_latency_seconds", "Latency when querying the JIRA API", ["action"]
)
jira_request_time_transitions = jira_request_time.labels(action="transitions")
jira_request_time_close = jira_request_time.labels(action="close")
jira_request_time_update = jira_request_time.labels(action="update")
jira_request_time_create = jira_request_time.labels(action="create")

request_time = prometheus.Histogram(
    "request_latency_seconds", "Latency of incoming requests", ["endpoint"]
)
request_time_generic_issues = request_time.labels(endpoint="/issues")
request_time_qualified_issues = request_time.labels(
    endpoint="/issues/<project>/<issue_type>"
)


class Error(Exception):
    """All local errors."""
    pass


def prepare_group_label_key(gk):
    """Create a unique key by hashing an alert group."""
    hash_label = hashlib.sha1(gk.encode()).hexdigest()
    return hash_label[0:10]


def prepare_tags(common_labels):
    """Get JIRA tags from alert labels."""
    tags_whitelist = ["severity", "dc", "env", "perimeter", "team", "jiralert"]
    tags = ["alert"]
    for k, v in common_labels.items():
        if k in tags_whitelist:
            tags.append("%s:%s" % (k, v))
        if k == "tags":
            tags.extend([tag.strip() for tag in v.split(",") if tag])
    return tags


class Event(object):

    def __init__(self, project, issue_type, request, response):
        self.event = id(self)
        self.timestamp = time.time()
        self.project = project
        self.issue_type = issue_type
        self.request = request
        self.response = response


class Manager(object):
    """Issue manager."""
    JINJA_ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT_DIR))

    SUMMARY_TMPL = JINJA_ENV.get_template("templates/summary.template")
    DESCRIPTION_TMPL = JINJA_ENV.get_template("templates/description.template")
    DESCRIPTION_BOUNDARY = "_-- Alertmanager -- [only edit above]_"

    # Order for the search query is important for the query performance. It relies
    # on the 'alert_group_key' field in the description that must not be modified.
    SEARCH_QUERY = (
        'project = "{project}" and '
        'issuetype = "{issuetype}" and '
        'labels = "alert" and '
        "status not in ({status}) and "
        'labels = "jiralert:{group_label_key}"'
    )

    logger = logging.getLogger(__name__)

    def __init__(
        self,
        basic_auth=None,
        server=None,
        resolve_transitions=(),
        resolved_status=(),
        threadpool=None,
    ):
        self.jira = None
        self.basic_auth = basic_auth
        self.server = server
        self.resolve_transitions = resolve_transitions
        self.resolved_status = resolved_status
        self.threadpool = threadpool
        # TODO: Keep an history of the last handled payloads and associated tickets.
        # (updated or created). Display that on the UI.
        self.history = collections.deque(20 * [None], 20)

    def connect(self):
        self.logger.info("Connecting to %s" % self.server)
        self.jira = JIRA(basic_auth=self.basic_auth, server=self.server)
        self.logger.info("Connected to %s" % self.server)

    def ready(self):
        return bool(self.jira)

    def shutdown(self):
        self.jira.close()
        self.jira = None
        if self.threadpool:
            self.threadpool.stop()
            self.threadpool = None

    def record(self, project, issue_type, request, response):
        event = Event(project, issue_type, request, response)
        self.history.appendleft(event)

    def response(self, status, code, issues=None):
        return {"status": status, "issues": issues}, code

    @jira_errors_transitions.count_exceptions()
    @jira_request_time_transitions.time()
    def transitions(self, issue):
        return self.jira.transitions(issue)

    @jira_errors_close.count_exceptions()
    @jira_request_time_close.time()
    def close(self, issue, tid):
        return self.jira.transition_issue(issue, tid)

    @jira_errors_update.count_exceptions()
    @jira_request_time_update.time()
    def update_issue(self, issue, summary, description, tags):
        custom_desc = issue.fields.description.rsplit(self.DESCRIPTION_BOUNDARY, 1)[0]

        # Merge expected tags and existing ones
        fields = {"labels": list(set(issue.fields.labels + tags))}

        return issue.update(
            summary=summary,
            fields=fields,
            description="%s\n\n%s\n%s"
            % (custom_desc.strip(), self.DESCRIPTION_BOUNDARY, description),
        )

    @jira_errors_create.count_exceptions()
    @jira_request_time_create.time()
    def create_issue(self, project, issue_type, summary, description, tags):
        return self.jira.create_issue(
            {
                "project": {"key": project},
                "summary": summary,
                "description": "%s\n\n%s" % (self.DESCRIPTION_BOUNDARY, description),
                "issuetype": {"name": issue_type},
                "labels": tags,
            }
        )

    @request_time_generic_issues.time()
    def post_issues(self, payload):
        """
        This endpoint accepts a JSON encoded notification according to the version 3 or 4
        of the generic webhook of the Prometheus Alertmanager.
        """

        common_labels = payload["commonLabels"]
        if "issue_type" not in common_labels or "project" not in common_labels:
            self.logger.error(
                "/issue, required commonLabels not found: issue_type or project"
            )
            project = None
            issue_type = None
            resp = self.response(
                "Required commonLabels not found: issue_type or project", 400
            )
        else:
            issue_type = common_labels["issue_type"]
            project = common_labels["project"]
            resp = self.do_file_issue(project, issue_type, payload)

        self.record(project, issue_type, payload, resp)
        return resp

    @request_time_qualified_issues.time()
    def post_issues_with_project(self, project, issue_type, payload):
        """
        This endpoint accepts a JSON encoded notification according to the version 3 or 4
        of the generic webhook of the Prometheus Alertmanager.
        """
        if payload["version"] not in ["3", "4"]:
            self.logger.error(
                "/issue, unknown message version: %s" % payload["version"]
            )
            resp = self.response("unknown message version %s" % payload["version"], 400)
        else:
            resp = self.do_file_issue(project, issue_type, payload)

        self.record(project, issue_type, payload, resp)
        return resp

    def update_or_resolve_issue(
        self, project, issue_type, issue, resolved, summary, description, tags
    ):
        """Update and maybe resolve an issue."""
        resolved = False
        self.logger.debug(
            "issue (%s, %s), jira issue found: %s" % (project, issue_type, issue.key)
        )

        # Try different possible transitions for resolved incidents
        # in order of preference. Different ones may work for different boards.
        if resolved:
            valid_trans = [
                t
                for t in self.transitions(issue)
                if t["name"].lower() in self.resolve_transitions
            ]
            if valid_trans:
                self.close(issue, valid_trans[0]["id"])
                resolved = True
            else:
                self.logger.warning("Unable to find transition to close %s" % issue)

        # Update the base information regardless of the transition.
        self.update_issue(issue, summary, description, tags)
        self.logger.info(
            "issue (%s, %s), %s updated" % (project, issue_type, issue.key)
        )
        return resolved

    def do_file_issue(self, project, issue_type, payload):
        if not self.ready():
            return self.response("Not ready yet", 503)

        if payload["version"] not in ["3", "4"]:
            self.logger.error(
                "issue (%s, %s), unknown message version: %s"
                % (project, issue_type, payload["version"])
            )
            return self.response("unknown message version %s" % payload["version"], 400)

        if self.threadpool:
            # We want a separate thread pool here to avoid blocking incoming
            # requests.
            self.threadpool.callInThread(
                self.do_file_issue_async, project, issue_type, payload
            )
            return self.response("OK (async)", 201)
        else:
            issues = self.do_file_issue_sync(project, issue_type, payload)
            return self.response("OK", 200, issues)

    def do_file_issue_async(self, project, issue_type, data):
        try:
            issues = self.do_file_issue_sync(project, issue_type, data)
            resp = self.response("OK", 200, issues)
        except JIRAError as e:
            resp = self.response(str(e), 503)

        # Record a fake response for async requests.
        self.record(project, issue_type, data, resp)

    @errors.count_exceptions()
    def do_file_issue_sync(self, project, issue_type, data):
        issues = {"created": [], "found": [], "updated": [], "resolved": []}

        self.logger.info("issue: %s %s" % (project, issue_type))

        resolved = data["status"] == "resolved"
        tags = prepare_tags(data["commonLabels"])
        tags.append("jiralert:%s" % prepare_group_label_key(data["groupKey"]))

        description = self.DESCRIPTION_TMPL.render(data)
        summary = self.SUMMARY_TMPL.render(data)

        # If there's already a ticket for the incident, update it and close if necessary.
        query = self.SEARCH_QUERY.format(
            project=project,
            issuetype=issue_type,
            status=",".join(self.resolved_status),
            group_label_key=prepare_group_label_key(data["groupKey"]),
        )

        self.logger.debug(query)
        result = self.jira.search_issues(query) or []
        # sort issue by key to have them in order of creation.
        sorted(result, key=lambda i: i.key)
        issues["found"] = [issue.permalink() for issue in result]

        for issue in result:
            resolved = self.update_or_resolve_issue(
                project, issue_type, issue, resolved, summary, description, tags
            )
            issues["resolved" if resolved else "updated"].append(issue.permalink())
        if not result:
            # Do not create an issue for resolved incidents that were never filed.
            if not resolved:
                issue = self.create_issue(
                    project, issue_type, summary, description, tags
                )
                issues["created"].append(issue.permalink())
                self.logger.info(
                    "issue (%s, %s), new issue created (%s)"
                    % (project, issue_type, issue.key)
                )
        return issues
