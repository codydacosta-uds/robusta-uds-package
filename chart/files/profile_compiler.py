# Copyright 2026 Defense Unicorns
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Defense-Unicorns-Commercial

"""Compile package Profiles into exact Robusta 0.48.0 playbooks."""

import base64
import json
import os
import re
import time
from hashlib import sha256
from pathlib import Path

from profile_model import (
    NOISE_IGNORE, POD_HEALTH_RESOURCES, TRIGGER_KIND, coarse_includes, normalize_config,
)

PROFILE_PREFIX = "UdsProfileV2"
CHANGE_SINK = "profile-change-relay"
HEALTH_SINK = "profile-health-relay"
EVENT_CONTEXT_KINDS = {"Pod", "Deployment", "DaemonSet", "ReplicaSet", "StatefulSet", "Job", "Node"}


def _scope(profile, resource):
    namespaces = profile["scope"]["namespaces"]
    labels = profile["scope"]["labels"]
    entry = {}
    if namespaces:
        entry["namespace"] = [re.escape(value) for value in namespaces]
    names = resource.get("names", [])
    if names:
        entry["name"] = [re.escape(value) for value in names]
    if labels:
        entry["labels"] = [f"{key}={re.escape(value)}" for key, value in sorted(labels.items())]
    return {"include": [entry]} if entry else {}


def _name(profile, signal, kind, operation):
    safe_kind = re.sub(r"[^A-Za-z0-9]", "", kind)
    safe_signal = re.sub(r"[^A-Za-z0-9]", "", signal.title())
    safe_operation = re.sub(r"[^A-Za-z0-9]", "", operation.title())
    digest = profile["id"].rsplit("-", 1)[-1]
    return f"{PROFILE_PREFIX}{digest}{safe_signal}{safe_kind}{safe_operation}"


def _marker(profile, signal, operation, kind):
    namespace = "cluster-scoped" if not profile["scope"]["namespaces"] else "$namespace"
    return f"UDS_PROFILE_V2|{profile['id']}|{signal}|{operation}|{kind}|{namespace}|$name"


def _finding_override(profile, signal, operation, kind):
    return {"customise_finding": {
        "severity": profile["notify"]["severity"].upper(),
        "title": _marker(profile, signal, operation, kind),
        "aggregation_key": f"uds-profile-v2/{profile['id']}/{signal}/{operation}/{kind}",
    }}


def _change_playbook(profile, resource, operation, signal, categories=None):
    kind = resource["kind"]
    scope = _scope(profile, resource)
    params = {"scope": scope} if scope else {}
    if operation == "update":
        params["change_filters"] = {
            "include": coarse_includes(kind, categories or []),
            "ignore": list(NOISE_IGNORE),
        }
    actions = [{"resource_babysitter": {}}, _finding_override(profile, signal, operation, kind)]
    return {
        "name": _name(profile, signal, kind, operation),
        "triggers": [{f"on_{TRIGGER_KIND[kind]}_{operation}": params}],
        "actions": actions,
        "sinks": [CHANGE_SINK],
    }


def _pod_health_scope(profile):
    # Workload names and labels describe the owning workload, not necessarily
    # generated Pods. Namespace is the only safe coarse trigger scope; the
    # package ownership action performs the exact workload check before findings.
    namespaces = profile["scope"]["namespaces"]
    return {"include": [{"namespace": [re.escape(value) for value in namespaces]}]}


def _ownership_params(profile):
    resources = {
        item["kind"]: item["names"]
        for item in profile["resources"]
        if item["kind"] in POD_HEALTH_RESOURCES
    }
    return {"resources": resources, "labels": profile["scope"]["labels"]}


def _health_playbook(profile, signal):
    pod_scope = _pod_health_scope(profile)
    severity = profile["notify"]["severity"].upper()
    aggregation = f"uds-profile-v2/{profile['id']}/health/{signal}/Pod"
    customise = _finding_override(profile, "health", signal, "Pod")
    events = profile["context"]["kubernetesEvents"]
    ownership = {"profile_ownership_filter": _ownership_params(profile)}
    if signal == "crashLoop":
        actions = [ownership, {"create_finding": {"title": _marker(profile, "health", signal, "Pod"), "aggregation_key": aggregation, "severity": severity}}]
        if events: actions.append({"pod_events_enricher": {}})
        actions.append(customise)
        trigger = {"on_pod_crash_loop": {"scope": pod_scope, "restart_reason": "CrashLoopBackOff"}}
    elif signal == "imagePullFailure":
        actions = [ownership, {"image_pull_backoff_reporter": {}}, customise]
        trigger = {"on_image_pull_backoff": {"scope": pod_scope}}
    elif signal == "oomKill":
        actions = [ownership, {"pod_oom_killer_enricher": {"attach_logs": False, "container_memory_graph": False, "node_memory_graph": False, "dmesg_log": False}}, customise]
        trigger = {"on_pod_oom_killed": {"scope": pod_scope, "rate_limit": 3600}}
    elif signal == "podEviction":
        actions = [ownership, {"pod_evicted_enricher": {}}]
        if events: actions.append({"pod_events_enricher": {}})
        actions.append(customise)
        trigger = {"on_pod_evicted": {"scope": pod_scope}}
    elif signal == "jobFailure":
        job_resource = next(item for item in profile["resources"] if item["kind"] == "Job")
        job_scope = _scope(profile, job_resource)
        aggregation = f"uds-profile-v2/{profile['id']}/health/{signal}/Job"
        actions = [{"create_finding": {"title": _marker(profile, "health", signal, "Job"), "aggregation_key": aggregation, "severity": severity}}, {"job_info_enricher": {}}]
        if events:
            actions.extend([{"job_events_enricher": {}}, {"job_pod_enricher": {"events": True, "logs": False}}])
        actions.append(_finding_override(profile, "health", signal, "Job"))
        trigger = {"on_job_failure": {"scope": job_scope}}
    else:
        raise ValueError(f"unsupported health signal: {signal}")
    return {
        "name": _name(profile, "health", "Job" if signal == "jobFailure" else "Pod", signal),
        "triggers": [trigger], "actions": actions, "sinks": [HEALTH_SINK],
    }


def compile_profiles(raw_config):
    config = normalize_config(raw_config)
    playbooks = []
    for profile in config["profiles"]:
        if not profile["enabled"]:
            continue
        events = profile["monitor"]["changes"]["events"]
        categories = profile["monitor"]["drift"]["include"]
        for resource in profile["resources"]:
            for operation in events:
                playbooks.append(_change_playbook(profile, resource, operation, "change"))
            if categories:
                playbooks.append(_change_playbook(profile, resource, "update", "drift", categories))
        for signal, enabled in sorted(profile["monitor"]["health"].items()):
            if enabled:
                playbooks.append(_health_playbook(profile, signal))
    return config, sorted(playbooks, key=lambda item: item["name"])


def merge_runner_config(document, generated):
    existing = document.get("active_playbooks") or []
    document["active_playbooks"] = [item for item in existing if not str(item.get("name", "")).startswith(PROFILE_PREFIX)] + generated
    return document


def validate_exact_robusta(document):
    # These imports deliberately happen only in the compiler Job's packaged
    # Robusta image. Unit tests can compile without installing Robusta.
    from robusta.core.model.runner_config import RunnerConfig
    from robusta.core.playbooks.actions_registry import Action
    from robusta_playbooks.babysitter import BabysitterConfig, resource_babysitter
    from robusta_playbooks.common_actions import FindingFields, FindingOverrides, create_finding, customise_finding
    from robusta_playbooks.event_enrichments import EventEnricherParams, pod_events_enricher
    from robusta_playbooks.image_pull_backoff_enricher import image_pull_backoff_reporter
    from robusta_playbooks.job_actions import JobPodEnricherParams, job_events_enricher, job_info_enricher, job_pod_enricher
    from robusta_playbooks.oom_killer import pod_oom_killer_enricher
    from robusta_playbooks.pod_evicted_enrichments import pod_evicted_enricher
    from robusta.core.model.base_params import OomKillParams, RateLimitParams
    from profile_ownership import ProfileOwnershipParams, profile_ownership_filter

    RunnerConfig(**document)
    for function in (
        resource_babysitter, create_finding, customise_finding, pod_events_enricher,
        image_pull_backoff_reporter, pod_oom_killer_enricher, pod_evicted_enricher,
        job_info_enricher, job_events_enricher, job_pod_enricher,
        profile_ownership_filter,
    ):
        Action(function)
    BabysitterConfig()
    FindingFields(title="x", aggregation_key="x", severity="HIGH")
    FindingOverrides(title="x", aggregation_key="x", severity="HIGH")
    EventEnricherParams()
    JobPodEnricherParams(events=True, logs=False)
    OomKillParams(attach_logs=False, container_memory_graph=False, node_memory_graph=False, dmesg_log=False)
    RateLimitParams()
    ProfileOwnershipParams(resources={"Deployment": ["example"]}, labels={})


def main():
    import yaml
    from kubernetes import client, config as kube_config

    config_path = Path(os.environ.get("PROFILE_CONFIG_PATH", "/app/profiles.json"))
    namespace = os.environ.get("POD_NAMESPACE", "robusta")
    secret_name = os.environ.get("ROBUSTA_CONFIG_SECRET", "robusta-playbooks-config-secret")
    raw = json.loads(config_path.read_text())
    normalized, generated = compile_profiles(raw)
    kube_config.load_incluster_config()
    api = client.CoreV1Api()
    secret = api.read_namespaced_secret(secret_name, namespace)
    key = "active_playbooks.yaml"
    if not secret.data or key not in secret.data:
        raise RuntimeError(f"{secret_name} is missing {key}")
    document = yaml.safe_load(base64.b64decode(secret.data[key]).decode())
    merge_runner_config(document, generated)
    validate_exact_robusta(document)
    rendered = yaml.safe_dump(document, sort_keys=False)
    encoded = base64.b64encode(rendered.encode()).decode()
    applied_secret = secret
    if encoded != secret.data[key]:
        # Zarf deploys Helm resources with server-side apply under the `zarf`
        # field manager. Use that same manager so the generated field remains
        # upgradeable instead of creating an OpenAPI-Generator ownership conflict.
        body = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": secret_name, "namespace": namespace}, "data": {key: encoded}}
        # kubernetes-client 32.x exposes force but its generated wrapper always
        # selects JSON Patch. Call the same authenticated ApiClient directly so
        # this is a real server-side apply request.
        applied_secret = api.api_client.call_api(
            "/api/v1/namespaces/{namespace}/secrets/{name}", "PATCH",
            {"namespace": namespace, "name": secret_name},
            [("fieldManager", os.environ.get("CONFIG_FIELD_MANAGER", "zarf")), ("force", "true")],
            {"Accept": "application/json", "Content-Type": "application/apply-patch+yaml"},
            body=body, response_type="V1Secret", auth_settings=["BearerToken"],
            _return_http_data_only=True,
        )

    # Projected Secret refresh can lag package completion by more than a minute.
    # Roll only the runner after successful validation so it starts with the
    # exact generated file and no Profile events are lost during that window.
    rollout_token = f"{sha256(rendered.encode()).hexdigest()}-{applied_secret.metadata.resource_version}"
    apps = client.AppsV1Api()
    activated = apps.api_client.call_api(
        "/apis/apps/v1/namespaces/{namespace}/deployments/{name}", "PATCH",
        {"namespace": namespace, "name": "robusta-runner"}, [],
        {"Accept": "application/json", "Content-Type": "application/strategic-merge-patch+json"},
        body={"spec": {"template": {"metadata": {"annotations": {"uds.dev/profile-config-activation": rollout_token}}}}},
        response_type="V1Deployment", auth_settings=["BearerToken"], _return_http_data_only=True,
    )
    target_generation = activated.metadata.generation
    for _ in range(120):
        current = apps.read_namespaced_deployment("robusta-runner", namespace)
        desired = current.spec.replicas or 1
        if ((current.status.observed_generation or 0) >= target_generation
                and current.status.replicas == desired
                and current.status.updated_replicas == desired
                and current.status.available_replicas == desired):
            break
        time.sleep(1)
    else:
        raise RuntimeError("generated Profiles were valid, but robusta-runner activation did not complete within 120 seconds")
    print(f"Compiled {len(normalized['profiles'])} Profiles into {len(generated)} verified Robusta playbooks and activated the runner", flush=True)


if __name__ == "__main__":
    main()
