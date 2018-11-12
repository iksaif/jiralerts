# -*- coding: utf-8 -*-
import unittest
import httmock
import mock
import os
import prometheus_client
import sys
import json

from jiralerts import main
from jiralerts import issues

WEBHOOK_PAYLOAD = {
    "alerts": [
        {
            "annotations": {
                "documentation": "https://example.com/Foo",
                "summary": "Alert summary",
            },
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "https://example.com/foo",
            "labels": {"alertname": "Foo_Bar", "instance": "foo"},
            "startsAt": "2017-02-02T16:51:13.507955756Z",
            "status": "firing",
        },
        {
            "annotations": {
                "documentation": "https://example.com/Bar",
                "summary": "Alert summary",
            },
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "https://example.com/bar",
            "labels": {"alertname": "Foo_Bar", "instance": "bar"},
            "startsAt": "2017-02-02T16:51:13.507955756Z",
            "status": "firing",
        },
    ],
    "commonAnnotations": {
        "link": "https://example.com/Foo+Bar",
        "summary": "Alert summary",
    },
    "commonLabels": {
        "alertname": "Foo_Bar",
        "instance": "foo",
        "issue_type": "Alert",
        "project": "Foo",
    },
    "externalURL": "https://alertmanager.example.com",
    "groupLabels": {"alertname": "Foo_Bar", "dc": "par"},
    "receiver": "jiralert",
    "status": "firing",
    "version": "4",
    "groupKey": '{}/{}/{notify="default":{alertname="Foo_Bar", instance="foo"}',
}


@httmock.all_requests
def jira_mock(url, request):
    if url.path == "/rest/api/2/serverInfo":
        filename = "spec_jira_handshake.json"
    elif url.path == "/rest/api/2/field":
        filename = "spec_jira_fields.json"
    elif url.path == "/rest/api/2/search":
        filename = "spec_jira_search.json"
    elif url.path == "/FOO-1":
        filename = "spec_jira_update.json"
    else:
        raise Exception(url)

    filename = os.path.join(os.path.dirname(__file__), filename)
    with open(filename) as fd:
        data = fd.read()
    return data


class TestJiralerts(unittest.TestCase):
    JIRA_URL = "http://jira.foo"

    def setUp(self):
        self.maxDiff = 10000
        os.environ["JIRA_USERNAME"] = "username"
        os.environ["JIRA_PASSWORD"] = "password"

        testargs = ["jiralerts", "http://fake.jira.com/"]

        with mock.patch.object(sys, "argv", testargs):
            self.args = main.parse_args()
        self.registry = prometheus_client.CollectorRegistry(auto_describe=True)
        with httmock.HTTMock(jira_mock):
            self.gourde = main.create_app(self.args, registry=self.registry)
        self.client = self.gourde.app.test_client()

        # JIRA uses `sleep` internally and we do not want to wait.
        self._sleep_patcher = mock.patch('time.sleep')
        self._sleep_mock = self._sleep_patcher.start()

    def test_healthy(self):
        with httmock.HTTMock(jira_mock):
            r = self.client.get("/-/healthy")
        self.assertEqual(r.status_code, 200)

    def test_template_description(self):
        data = issues.Manager.prepare_data(WEBHOOK_PAYLOAD)
        description = issues.Manager.DESCRIPTION_TMPL.render(data)
        self.assertEqual(
            description,
            """h2. Common information
{noformat:borderStyle=none|bgColor=#FFFFFF}Group key: \
{}/{}/{notify="default":{alertname="Foo_Bar", instance="foo"}{noformat}
[Alertmanager|https://alertmanager.example.com/#/alerts?receiver=jiralert&filter=%7Balertname%3D%22Foo_Bar%22%7D]

_Common_Annotations_:
* *link*: https://example.com/Foo+Bar
* *summary*: Alert summary

_Common_Labels_:
* alertname: "Foo_Bar"
* instance: "foo"
* issue_type: "Alert"
* project: "Foo"


h2. Active alerts (total : 2)
•  ([documentation|https://example.com/Foo], [source|https://example.com/foo])\
{color:#fff} - 513bc547{color}
•  ([documentation|https://example.com/Bar], [source|https://example.com/bar])\
{color:#fff} - 910c556d{color}
""",
        )

    def test_template_summary(self):
        data = issues.Manager.prepare_data(WEBHOOK_PAYLOAD)
        summary = issues.Manager.SUMMARY_TMPL.render(data)
        self.assertEqual(summary, "Foo_Bar: Alert summary")

    def test_issues(self):
        for url in (
            "/issues/FOO/Alert",
            "/issues",
            "/api/issues/FOO/Alert",
            "/api/issues",
        ):
            with httmock.HTTMock(jira_mock):
                r = self.client.post(
                    url,
                    data=json.dumps(WEBHOOK_PAYLOAD),
                    content_type="application/json",
                )
            self.assertEqual(r.status_code, 200)
            expected = {
                "issues": {
                    "created": [],
                    "found": ["http://fake.jira.com/browse/FOO-1"],
                    "resolved": [],
                    "updated": ["http://fake.jira.com/browse/FOO-1"],
                },
                "status": "OK",
            }
            self.assertEqual(json.loads(r.data.decode("utf-8")), expected)


if __name__ == "__main__":
    unittest.main()
