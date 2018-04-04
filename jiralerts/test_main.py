# -*- coding: utf-8 -*-
import unittest

from jiralerts import main

WEBHOOK_PAYLOAD = {
    "alerts": [{
        "annotations": {
            "link": "https://example.com/Foo+Bar",
            "summary": "Alert summary"
        },
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "https://example.com",
        "labels": {
            "alertname": "Foo_Bar",
            "instance": "foo"
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
        "alertname": "Foo_Bar"
    },
    "receiver": "jiralert",
    "status": "firing",
    "version": "4",
    "groupKey": "{}/{}/{notify=\"default\":{alertname=\"Foo_Bar\", instance=\"foo\"}"
}


class TestJiralerts(unittest.TestCase):

    def setUp(self):
        self.maxDiff = 10000

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


h2. Active alerts (total : 1)
•  ([documentation|], [source|https://example.com])
""")

    def test_template_summary(self):
        summary = main.SUMMARY_TMPL.render(WEBHOOK_PAYLOAD)
        self.assertEqual(summary, "Foo_Bar: Alert summary")


if __name__ == '__main__':
    unittest.main()
