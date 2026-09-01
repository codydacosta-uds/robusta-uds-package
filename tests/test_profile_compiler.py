# Copyright 2026 Defense Unicorns
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Defense-Unicorns-Commercial

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
for module_name in ("profile_model", "profile_compiler"):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / f"chart/files/{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
model = sys.modules["profile_model"]
compiler = sys.modules["profile_compiler"]

CONFIG = {
    "schemaVersion": 1,
    "defaultDestinations": ["platform"],
    "destinations": {"platform": {"type": "mattermost", "secretKey": "platform-url"}},
    "profiles": [{
        "name": "gitlab-production",
        "scope": {
            "namespaces": ["gitlab", "gitlab-staging"],
            "labels": {"app.kubernetes.io/part-of": "gitlab"},
        },
        "resources": {
            "deployment": {"names": ["webservice", "sidekiq"]},
            "configmap": {},
            "job": {"names": ["backup"]},
        },
        "monitor": {
            "changes": {"events": ["create", "delete"]},
            "drift": {"include": ["image", "replicas", "resources", "configuration", "manifest"]},
            "health": {"crashLoop": True, "imagePullFailure": True, "oomKill": True, "podEviction": True, "jobFailure": True},
        },
        "context": {"diff": True, "kubernetesEvents": True},
        "notify": {"severity": "high", "destinations": ["platform"]},
    }],
}


class ProfileCompilerTest(unittest.TestCase):
    def test_compiles_deterministically(self):
        normalized, playbooks = compiler.compile_profiles(CONFIG)
        self.assertEqual((normalized, playbooks), compiler.compile_profiles(json.loads(json.dumps(CONFIG))))
        # Three resources x create/delete/update plus five health signals.
        self.assertEqual(len(playbooks), 14)
        self.assertEqual(playbooks, sorted(playbooks, key=lambda item: item["name"]))
        self.assertEqual(len({item["name"] for item in playbooks}), len(playbooks))

    def test_generated_scope_is_exact_and_escaped(self):
        _, playbooks = compiler.compile_profiles(CONFIG)
        deployment = next(item for item in playbooks if "DriftDeploymentUpdate" in item["name"])
        trigger = deployment["triggers"][0]["on_deployment_update"]
        scope = trigger["scope"]["include"][0]
        self.assertEqual(scope["namespace"], ["gitlab", "gitlab\\-staging"])
        self.assertEqual(scope["name"], ["webservice", "sidekiq"])
        self.assertEqual(scope["labels"], ["app.kubernetes.io/part-of=gitlab"])
        self.assertIn("image", trigger["change_filters"]["include"])
        self.assertIn("status", trigger["change_filters"]["ignore"])

    def test_create_delete_and_drift_use_distinct_markers(self):
        _, playbooks = compiler.compile_profiles(CONFIG)
        create = next(item for item in playbooks if "ChangeConfigMapCreate" in item["name"])
        drift = next(item for item in playbooks if "DriftConfigMapUpdate" in item["name"])
        create_title = create["actions"][-1]["customise_finding"]["title"]
        drift_title = drift["actions"][-1]["customise_finding"]["title"]
        self.assertIn("|change|create|ConfigMap|", create_title)
        self.assertIn("|drift|update|ConfigMap|", drift_title)
        self.assertEqual(create["sinks"], [compiler.CHANGE_SINK])

    def test_cluster_wide_trigger_omits_empty_scope(self):
        config = json.loads(json.dumps(CONFIG))
        config["profiles"][0] = {
            "name": "cluster-security",
            "resources": {"clusterrole": {}},
            "monitor": {"drift": {"include": ["permissions"]}},
        }
        _, playbooks = compiler.compile_profiles(config)
        params = playbooks[0]["triggers"][0]["on_clusterrole_update"]
        self.assertNotIn("scope", params)

    def test_health_uses_namespace_scope_then_exact_ownership_filter(self):
        _, playbooks = compiler.compile_profiles(CONFIG)
        crash = next(item for item in playbooks if "crashloop" in item["name"].lower())
        scope = crash["triggers"][0]["on_pod_crash_loop"]["scope"]["include"][0]
        self.assertEqual(scope, {"namespace": ["gitlab", "gitlab\\-staging"]})
        ownership = crash["actions"][0]["profile_ownership_filter"]
        self.assertEqual(ownership["resources"], {
            "Deployment": ["webservice", "sidekiq"], "Job": ["backup"]
        })
        self.assertEqual(ownership["labels"], {"app.kubernetes.io/part-of": "gitlab"})

    def test_health_actions_never_attach_logs_or_graphs(self):
        _, playbooks = compiler.compile_profiles(CONFIG)
        health = [item for item in playbooks if "Health" in item["name"]]
        self.assertEqual(len(health), 5)
        serialized = json.dumps(health)
        self.assertNotIn("report_crash_loop", serialized)
        self.assertNotIn("logs_enricher", serialized)
        oom = next(item for item in health if "oomkill" in item["name"].lower())
        params = next(action["pod_oom_killer_enricher"] for action in oom["actions"] if "pod_oom_killer_enricher" in action)
        self.assertEqual(params, {"attach_logs": False, "container_memory_graph": False, "node_memory_graph": False, "dmesg_log": False})
        job = next(item for item in health if "jobfailure" in item["name"].lower())
        self.assertFalse(next(action["job_pod_enricher"]["logs"] for action in job["actions"] if "job_pod_enricher" in action))
        self.assertEqual(job["sinks"], [compiler.HEALTH_SINK])

    def test_merge_preserves_unowned_playbooks_and_replaces_profiles(self):
        document = {"active_playbooks": [{"name": "Upstream"}, {"name": "UdsProfileV2old"}]}
        generated = [{"name": "UdsProfileV2new"}]
        result = compiler.merge_runner_config(document, generated)
        self.assertEqual(result["active_playbooks"], [{"name": "Upstream"}, {"name": "UdsProfileV2new"}])

    def test_implicit_health_defaults_compile_without_monitor_block(self):
        config = json.loads(json.dumps(CONFIG))
        config["profiles"][0] = {
            "name": "minimal-app",
            "scope": {"namespaces": ["gitlab"]},
            "resources": {"deployment": {"names": ["webservice"]}},
        }
        _, playbooks = compiler.compile_profiles(config)
        self.assertEqual(len(playbooks), 4)
        self.assertTrue(all("Health" in item["name"] for item in playbooks))

    def test_disabled_profile_generates_nothing(self):
        config = json.loads(json.dumps(CONFIG))
        config["profiles"][0]["enabled"] = False
        self.assertEqual(compiler.compile_profiles(config)[1], [])


if __name__ == "__main__":
    unittest.main()
