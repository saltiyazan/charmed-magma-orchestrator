#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import Mock, call, patch

from ops.testing import Harness

from charm import MagmaOrc8rLteCharm


class Test(unittest.TestCase):
    @patch(
        "charm.KubernetesServicePatch",
        lambda charm, ports, additional_labels, additional_annotations: None,
    )
    def setUp(self):
        self.harness = Harness(MagmaOrc8rLteCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch("ops.model.Container.push")
    def test_given_new_charm_when_on_install_event_then_config_files_are_created(self, patch_push):
        event = Mock()

        self.harness.charm._on_install(event)

        calls = [
            call(
                "/var/opt/magma/configs/orc8r/metricsd.yml",
                'prometheusQueryAddress: "http://orc8r-prometheus:9090"\n'
                'alertmanagerApiURL: "http://orc8r-alertmanager:9093/api/v2"\n'
                'prometheusConfigServiceURL: "http://orc8r-prometheus:9100/v1"\n'
                'alertmanagerConfigServiceURL: "http://orc8r-alertmanager:9101/v1"\n'
                '"profile": "prometheus"\n',
            ),
            call(
                "/var/opt/magma/configs/orc8r/analytics.yml",
                '"appID": ""\n'
                '"appSecret": ""\n'
                '"categoryName": "magma"\n'
                '"exportMetrics": false\n'
                '"metricExportURL": ""\n'
                '"metricsPrefix": ""\n',
            ),
        ]
        patch_push.assert_has_calls(calls)
