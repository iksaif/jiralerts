# -*- coding: utf-8 -*-
import pytest
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
    def test(self):
        pass

        # TODO write tests with a JIRA mock.


if __name__ == '__main__':
    unittest.main()
