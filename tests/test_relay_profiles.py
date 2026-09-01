# Copyright 2026 Defense Unicorns
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Defense-Unicorns-Commercial

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("relay", ROOT / "chart/files/relay.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)

CONFIG = {
    "defaultSinks": ["alerts-default"],
    "sinks": {
        "alerts-default": {"type": "mattermost", "secretKey": "alerts-default-url"},
        "team-alerts": {"type": "slack", "secretKey": "team-url"},
    },
    "alertProfiles": [
        {
            "name": "zarf-namespaced-resources",
            "namespaces": ["zarf"],
            "resources": sorted(relay.NAMESPACED_RESOURCES),
        },
        {
            "name": "team-resources",
            "namespaces": ["team-one", "team-two"],
            "resources": ["ConfigMap", "Deployment"],
            "sinks": ["team-alerts"],
        },
    ],
    "clusterAlertProfiles": [
        {
            "name": "cluster-scoped-resources",
            "resources": sorted(relay.CLUSTER_RESOURCES),
        }
    ],
}

PATHS = {
    "ConfigMap": "data.application.yaml",
    "DaemonSet": "spec.template.spec.volumes.0",
    "Deployment": "spec.template.spec.containers.0.image",
    "HorizontalPodAutoscaler": "spec.maxReplicas",
    "Ingress": "spec.rules.0.host",
    "Job": "spec.template.spec.containers.0.image",
    "Pod": "spec.containers.0.env.0.value",
    "ReplicaSet": "spec.template.spec.containers.0.resources.limits.cpu",
    "Secret": "metadata.labels.owner",
    "Service": "spec.ports.0.port",
    "ServiceAccount": "imagePullSecrets.0.name",
    "StatefulSet": "spec.replicas",
    "ClusterRole": "rules.0.verbs",
    "ClusterRoleBinding": "subjects.0.name",
    "Namespace": "metadata.labels.team",
    "Node": "spec.taints.0.effect",
    "PersistentVolume": "spec.capacity.storage",
}

EXPECTED_FORMAT = {
    "ConfigMap": ("Data key", "Configuration data changes"),
    "DaemonSet": ("Volume", "Workload manifest changes"),
    "Deployment": ("Container image", "Workload manifest changes"),
    "HorizontalPodAutoscaler": ("Maximum replicas", "Autoscaling changes"),
    "Ingress": ("Host", "Network configuration changes"),
    "Job": ("Container image", "Workload manifest changes"),
    "Pod": ("Container environment", "Workload manifest changes"),
    "ReplicaSet": ("Container resources", "Workload manifest changes"),
    "Secret": ("Secret label", "Secret changes"),
    "Service": ("Service port", "Network configuration changes"),
    "ServiceAccount": ("Image pull secret", "Access-control changes"),
    "StatefulSet": ("Replica count", "Workload manifest changes"),
    "ClusterRole": ("RBAC rule", "Access-control changes"),
    "ClusterRoleBinding": ("Subject", "Access-control changes"),
    "Namespace": ("Namespace label", "Manifest changes"),
    "Node": ("Taint", "Manifest changes"),
    "PersistentVolume": ("Capacity", "Storage configuration changes"),
}


class RelayProfilesTest(unittest.TestCase):
    def test_configuration_is_valid(self):
        self.assertIs(relay.validate_config(CONFIG), CONFIG)
        default_type = json.loads(json.dumps(CONFIG))
        default_type["sinks"]["alerts-default"].pop("type")
        self.assertIs(relay.validate_config(default_type), default_type)

    def test_every_supported_resource_routes_and_renders(self):
        self.assertEqual(set(PATHS), relay.SUPPORTED_RESOURCES)
        for kind, path in PATHS.items():
            with self.subTest(kind=kind):
                cluster = kind in relay.CLUSTER_RESOURCES
                namespace = "cluster-scoped" if cluster else "zarf"
                sinks, profiles, is_cluster = relay.matching_sinks(kind, namespace, CONFIG)
                self.assertEqual(sinks, ["alerts-default"])
                self.assertEqual(is_cluster, cluster)
                message = f"{kind} changed: {namespace}/example\n*{path}*: before ==> after"
                alert = relay.render_alert(message, relay.RED if cluster else relay.YELLOW, profiles)
                attachment = alert["attachments"][0]
                self.assertEqual(attachment["color"], relay.RED if cluster else relay.YELLOW)
                self.assertIn(f"{kind} changed", attachment["fallback"])
                expected_label, expected_section = EXPECTED_FORMAT[kind]
                self.assertIn(f"**{expected_label}:**", attachment["text"])
                self.assertIn(f"**{expected_section}**", attachment["text"])
                fields = {field["title"]: field["value"] for field in attachment["fields"]}
                self.assertEqual(fields["Kind"], kind)
                self.assertEqual(fields["Scope"], "Cluster" if cluster else "Namespaced")

    def test_profile_override_uses_named_sink(self):
        sinks, profiles, cluster = relay.matching_sinks("Deployment", "team-one", CONFIG)
        self.assertEqual(sinks, ["team-alerts"])
        self.assertEqual(profiles, ["team-resources"])
        self.assertFalse(cluster)

    def test_namespace_matching_is_exact(self):
        self.assertEqual(relay.matching_sinks("ConfigMap", "zarf-extra", CONFIG)[0], [])

    def test_unmatched_resource_is_dropped(self):
        self.assertEqual(relay.matching_sinks("Service", "team-one", CONFIG)[0], [])

    def test_invalid_resource_and_sink_fail_validation(self):
        bad_resource = json.loads(json.dumps(CONFIG))
        bad_resource["alertProfiles"][0]["resources"] = ["NetworkPolicy"]
        with self.assertRaisesRegex(ValueError, "unsupported values"):
            relay.validate_config(bad_resource)
        bad_sink = json.loads(json.dumps(CONFIG))
        bad_sink["alertProfiles"][0]["sinks"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "unsupported values"):
            relay.validate_config(bad_sink)
        bad_type = json.loads(json.dumps(CONFIG))
        bad_type["sinks"]["alerts-default"]["type"] = "teams"
        with self.assertRaisesRegex(ValueError, "mattermost or slack"):
            relay.validate_config(bad_type)

    def test_slack_rendering_uses_slack_markdown(self):
        text = relay.render_alert(
            "ConfigMap changed: zarf/example\n*data.status*: before ==> after",
            profiles=["zarf-namespaced-resources"],
            sink_type="slack",
        )["attachments"][0]["text"]
        self.assertIn("*Summary:*", text)
        self.assertIn("*Configuration data changes*", text)
        self.assertIn("*Data key:* status", text)
        self.assertNotIn("**", text)
        self.assertNotIn("```diff", text)
        self.assertIn("-before", text)
        self.assertIn("+after", text)

    def test_secret_values_are_redacted(self):
        text = relay.render_alert(
            "Secret changed: zarf/example\n*data.password*: super-secret ==> newer-secret"
        )["attachments"][0]["text"]
        self.assertNotIn("super-secret", text)
        self.assertNotIn("newer-secret", text)
        self.assertIn("Value changed (redacted)", text)

    def test_sink_url_reads_only_configured_secret_key(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = relay.SINK_DIRECTORY
            relay.SINK_DIRECTORY = Path(directory)
            try:
                (Path(directory) / "team-url").write_text("https://alerts.example/hooks/test")
                self.assertEqual(
                    relay.sink_url(CONFIG, "team-alerts"),
                    "https://alerts.example/hooks/test",
                )
            finally:
                relay.SINK_DIRECTORY = previous


if __name__ == "__main__":
    unittest.main()
