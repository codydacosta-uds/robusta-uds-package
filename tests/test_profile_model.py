# Copyright 2026 Defense Unicorns
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Defense-Unicorns-Commercial

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("profile_model", ROOT / "chart/files/profile_model.py")
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)

PROFILE = {
    "schemaVersion": 1,
    "defaultDestinations": ["platform"],
    "destinations": {"platform": {"type": "mattermost", "secretKey": "platform-url"}},
    "profiles": [{
        "name": "gitlab-production",
        "scope": {"namespaces": ["gitlab"], "labels": {"app.kubernetes.io/part-of": "gitlab"}},
        "resources": {"deployment": {"names": ["webservice"]}, "configmap": {}},
        "monitor": {
            "changes": {"events": ["create", "delete"]},
            "drift": {"include": ["image", "replicas", "resources", "configuration"]},
            "health": {"crashLoop": True},
        },
        "context": {"diff": True, "kubernetesEvents": True},
        "notify": {"severity": "high"},
    }],
}

LEGACY = {
    "defaultSinks": ["alerts-default"],
    "sinks": {"alerts-default": {"type": "slack", "secretKey": "alerts-default-url"}},
    "namespacedAlertRules": [{"name": "zarf-resources", "namespaces": ["zarf"], "resources": ["ConfigMap", "Deployment"]}],
    "clusterAlertRules": [{"name": "cluster-resources", "resources": ["Node"]}],
}


class ProfileModelTest(unittest.TestCase):
    def test_v2_normalizes_deterministically(self):
        first = model.normalize_config(PROFILE)
        second = model.normalize_config(json.loads(json.dumps(PROFILE)))
        self.assertEqual(first, second)
        self.assertEqual(first["profiles"][0]["id"], model.profile_id("gitlab-production"))
        self.assertEqual(model.canonical_json(PROFILE), model.canonical_json(PROFILE))

    def test_builtin_profile_is_health_only_and_narrow(self):
        lines = (ROOT / "chart/values.yaml").read_text().splitlines()
        index = lines.index("defaultProfileConfig: >-")
        config = json.loads(lines[index + 1].strip())
        normalized = model.normalize_config(config)
        self.assertEqual([profile["name"] for profile in normalized["profiles"]], ["uds-core-health"])
        profile = normalized["profiles"][0]
        self.assertEqual(profile["scope"]["namespaces"], ["zarf"])
        self.assertEqual(
            [resource["kind"] for resource in profile["resources"]],
            ["DaemonSet", "Deployment", "Job", "ReplicaSet", "StatefulSet"],
        )
        self.assertEqual(profile["monitor"]["changes"]["events"], [])
        self.assertEqual(profile["monitor"]["drift"]["include"], [])
        self.assertTrue(all(profile["monitor"]["health"][signal] for signal in model.HEALTH_SIGNALS))

    def test_simple_profile_uses_standard_destination_defaults(self):
        candidate = copy.deepcopy(PROFILE)
        candidate.pop("defaultDestinations")
        candidate.pop("destinations")
        normalized = model.normalize_config(candidate)
        self.assertEqual(normalized["defaultDestinations"], ["alerts-default"])
        self.assertEqual(
            normalized["destinations"],
            {"alerts-default": {"type": "mattermost", "secretKey": "alerts-default-url"}},
        )
        self.assertEqual(normalized["profiles"][0]["notify"]["destinations"], ["alerts-default"])

    def test_single_custom_destination_becomes_default(self):
        candidate = copy.deepcopy(PROFILE)
        candidate.pop("defaultDestinations")
        candidate["destinations"] = {"team": {"type": "slack", "secretKey": "team-url"}}
        normalized = model.normalize_config(candidate)
        self.assertEqual(normalized["defaultDestinations"], ["team"])

        candidate["destinations"]["other"] = {"type": "mattermost", "secretKey": "other-url"}
        with self.assertRaisesRegex(ValueError, "defaultDestinations is required"):
            model.normalize_config(candidate)

    def test_legacy_translates_to_observed_drift_profiles(self):
        normalized = model.normalize_config(LEGACY)
        self.assertEqual(normalized["schemaVersion"], 1)
        self.assertEqual(len(normalized["profiles"]), 2)
        self.assertEqual(normalized["profiles"][0]["monitor"]["drift"]["include"], ["manifest"])
        self.assertEqual(normalized["destinations"]["alerts-default"]["type"], "slack")

    def test_scope_and_resource_validation(self):
        for mutate, message in [
            (lambda p: p["profiles"][0]["scope"].update(namespaces=[]), "requires scope.namespaces"),
            (lambda p: p["profiles"][0]["resources"].update(node={}), "cannot set scope.namespaces"),
            (lambda p: p["profiles"][0]["resources"].update(networkpolicy={}), "unsupported resource"),
            (lambda p: p["profiles"][0]["scope"].update(labels={"bad key": "x"}), "label key/value"),
            (lambda p: p["profiles"][0]["scope"].update(labels={"team": "bad=value"}), "label key/value"),
            (lambda p: p["profiles"][0]["scope"].update(namespaces=["not.a.namespace"]), "exact namespace"),
        ]:
            with self.subTest(message=message):
                candidate = copy.deepcopy(PROFILE)
                mutate(candidate)
                with self.assertRaisesRegex(ValueError, message):
                    model.normalize_config(candidate)

    def test_old_or_overformatted_resource_shapes_fail_clearly(self):
        candidate = copy.deepcopy(PROFILE)
        candidate["profiles"][0]["resources"] = ["deployment"]
        with self.assertRaisesRegex(ValueError, "resources must map lowercase Kubernetes resource types"):
            model.normalize_config(candidate)
        candidate["profiles"][0]["resources"] = {"deployment": {"kind": "Deployment"}}
        with self.assertRaisesRegex(ValueError, "unknown fields: kind"):
            model.normalize_config(candidate)

    def test_resource_kinds_are_case_insensitive_and_normalize(self):
        candidate = copy.deepcopy(PROFILE)
        candidate["profiles"][0]["resources"] = {"DEPLOYMENT": {"names": ["webservice"]}, "ConfigMap": {}}
        resources = model.normalize_config(candidate)["profiles"][0]["resources"]
        self.assertEqual([item["kind"] for item in resources], ["Deployment", "ConfigMap"])
        candidate["profiles"][0]["resources"]["deployment"] = {}
        with self.assertRaisesRegex(ValueError, "more than once"):
            model.normalize_config(candidate)

    def test_drift_categories_must_apply_to_every_resource(self):
        candidate = copy.deepcopy(PROFILE)
        candidate["profiles"][0]["monitor"]["drift"]["include"] = ["image"]
        with self.assertRaisesRegex(ValueError, "ConfigMap has no applicable drift category"):
            model.normalize_config(candidate)

    def test_health_defaults_apply_by_resource_and_allow_opt_out(self):
        normalized = model.normalize_config(PROFILE)["profiles"][0]["monitor"]["health"]
        self.assertTrue(normalized["crashLoop"])
        self.assertTrue(normalized["imagePullFailure"])
        self.assertTrue(normalized["oomKill"])
        self.assertTrue(normalized["podEviction"])
        self.assertFalse(normalized["jobFailure"])

        candidate = copy.deepcopy(PROFILE)
        candidate["profiles"][0]["monitor"]["health"] = {"oomKill": False}
        health = model.normalize_config(candidate)["profiles"][0]["monitor"]["health"]
        self.assertFalse(health["oomKill"])
        self.assertTrue(health["crashLoop"])

        candidate["profiles"][0]["monitor"]["health"] = {"defaults": False, "jobFailure": True}
        with self.assertRaisesRegex(ValueError, "requires a job resource"):
            model.normalize_config(candidate)

    def test_non_workload_profile_requires_explicit_change_monitoring(self):
        candidate = copy.deepcopy(PROFILE)
        candidate["profiles"][0]["resources"] = {"configmap": {}}
        candidate["profiles"][0]["monitor"] = {}
        with self.assertRaisesRegex(ValueError, "must monitor changes, drift, or health"):
            model.normalize_config(candidate)

    def test_exact_semantic_classification(self):
        cases = {
            ("Deployment", "spec.template.spec.containers[0].image"): "image",
            ("Deployment", "spec.template.spec.containers.0.image"): "image",
            ("Deployment", "spec.template.spec.initContainers[1].image"): "image",
            ("Deployment", "spec.template.spec.containers[0].imagePullPolicy"): "manifest",
            ("Deployment", "spec.replicas"): "replicas",
            ("Deployment", "spec.template.spec.containers[0].resources.limits.cpu"): "resources",
            ("Pod", "spec.containers[0].env[0].value"): "environment",
            ("StatefulSet", "spec.template.spec.volumes[0].secret.secretName"): "volumes",
            ("ConfigMap", "data.application.yaml"): "configuration",
            ("Service", "spec.ports[0].port"): "networking",
            ("Ingress", "spec.rules[0].host"): "networking",
            ("HorizontalPodAutoscaler", "spec.maxReplicas"): "autoscaling",
            ("ClusterRole", "rules[0].verbs"): "permissions",
            ("ClusterRoleBinding", "subjects[0].name"): "permissions",
            ("ServiceAccount", "imagePullSecrets[0].name"): "permissions",
            ("Node", "spec.taints[0].effect"): "scheduling",
            ("PersistentVolume", "spec.capacity.storage"): "storage",
            ("Namespace", "metadata.labels.owner"): "labels",
            ("ConfigMap", "metadata.annotations.checksum"): "annotations",
        }
        for (kind, path), expected in cases.items():
            with self.subTest(kind=kind, path=path):
                self.assertEqual(model.semantic_category(kind, path), expected)
        self.assertEqual(model.matching_categories("Deployment", ["spec.template.spec.containers[0].imagePullPolicy"], ["image"]), [])
        self.assertEqual(model.matching_categories("Deployment", ["spec.template.spec.containers[0].image"], ["image"]), ["image"])

    def test_unknown_fields_fail_early(self):
        candidate = copy.deepcopy(PROFILE)
        candidate["profiles"][0]["monitor"]["drift"]["robustaFilter"] = ["spec"]
        with self.assertRaisesRegex(ValueError, "unknown fields: robustaFilter"):
            model.normalize_config(candidate)


if __name__ == "__main__":
    unittest.main()
