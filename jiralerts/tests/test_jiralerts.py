# -*- coding: utf-8 -*-
import unittest
import httmock
import os

from jiralerts import main


WEBHOOK_PAYLOAD = {
    "alerts": [{
        "annotations": {
            "documentation": "https://example.com/Foo",
            "summary": "Alert summary"
        },
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "https://example.com/foo",
        "labels": {
            "alertname": "Foo_Bar",
            "instance": "foo"
        },
        "startsAt": "2017-02-02T16:51:13.507955756Z",
        "status": "firing"
    }, {
        "annotations": {
            "documentation": "https://example.com/Bar",
            "summary": "Alert summary"
        },
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "https://example.com/bar",
        "labels": {
            "alertname": "Foo_Bar",
            "instance": "bar"
        },
        "startsAt": "2017-02-02T16:51:13.507955756Z",
        "status": "firing"
    }],
    "commonAnnotations": {
        "link": "https://example.com/Foo+Bar",
        "summary": "Alert summary"
    },
    "commonLabels": {
        "alertname": "Foo_Bar",
        "instance": "foo"
    },
    "externalURL": "https://alertmanager.example.com",
    "groupLabels": {
        "alertname": "Foo_Bar",
        "dc": "par",
    },
    "receiver": "jiralert",
    "status": "firing",
    "version": "4",
    "groupKey": "{}/{}/{notify=\"default\":{alertname=\"Foo_Bar\", instance=\"foo\"}"
}


@httmock.all_requests
def jira_mock(url, request):
    if url.path == '/rest/api/2/serverInfo':
        filename = 'spec_jira_handshake.json'
    elif url.path == '/rest/api/2/field':
        filename = 'spec_jira_fields.json'
    elif url.path == '/rest/api/2/search':
        filename = 'spec_jira_search.json'
    elif url.path == '/FOO-1':
        filename = 'spec_jira_update.json'
    else:
        raise Exception(url)
    filename = os.path.join(os.path.dirname(__file__), filename)
    with open(filename) as fd:
        data = fd.read()
    return data


class TestJiralerts(unittest.TestCase):

    JIRA_URL = 'http://jira.foo'

    def setUp(self):
        self.maxDiff = 10000
        os.environ['JIRA_USERNAME'] = 'username'
        os.environ['JIRA_PASSWORD'] = 'password'

    def test_app(self):
        self.assertNotEqual(main.app, None)

    def test_template_description(self):
        description = main.DESCRIPTION_TMPL.render(WEBHOOK_PAYLOAD)
        self.assertEqual(description, """h2. Common information

_Common_Annotations_:
* *link*: https://example.com/Foo+Bar
* *summary*: Alert summary

_Common_Labels_:
* alertname: "Foo_Bar"
* instance: "foo"


h2. Active alerts (total : 2)
•  ([documentation|https://example.com/Bar], [source|https://example.com/bar])
•  ([documentation|https://example.com/Foo], [source|https://example.com/foo])


Group key: {}/{}/{notify="default":{alertname="Foo_Bar", instance="foo"}""")

    def test_template_summary(self):
        summary = main.SUMMARY_TMPL.render(WEBHOOK_PAYLOAD)
        self.assertEqual(summary, "Foo_Bar: Alert summary")

    def test_do_file_issue_sync(self):
        with httmock.HTTMock(jira_mock):
            main.setup_app(self.JIRA_URL, "close issue", "closed")
            main.do_file_issue_sync('FOO', 'Alert', WEBHOOK_PAYLOAD)


if __name__ == '__main__':
    unittest.main()
