# Copyright 2026 Defense Unicorns
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Defense-Unicorns-Commercial

"""Profile validation, normalization, and semantic drift classification."""

import hashlib
import json
import re

NAMESPACED_RESOURCES = {
    "ConfigMap", "DaemonSet", "Deployment", "HorizontalPodAutoscaler", "Ingress",
    "Job", "Pod", "ReplicaSet", "Secret", "Service", "ServiceAccount", "StatefulSet",
}
CLUSTER_RESOURCES = {"ClusterRole", "ClusterRoleBinding", "Namespace", "Node", "PersistentVolume"}
SUPPORTED_RESOURCES = NAMESPACED_RESOURCES | CLUSTER_RESOURCES
RESOURCE_NAMES = {kind.lower(): kind for kind in SUPPORTED_RESOURCES}
WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Pod", "Job"}
TEMPLATE_WORKLOADS = WORKLOADS - {"Pod"}
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
DNS_SUBDOMAIN = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
CONFIG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
LABEL_KEY = re.compile(r"^(?:[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?$")
LABEL_VALUE = re.compile(r"^(?:[A-Za-z0-9](?:[-A-Za-z0-9_.]*[A-Za-z0-9])?)?$")
SEVERITIES = {"debug", "info", "low", "high"}
CHANGE_EVENTS = {"create", "delete"}
HEALTH_SIGNALS = {"crashLoop", "imagePullFailure", "oomKill", "jobFailure", "podEviction"}
POD_HEALTH_SIGNALS = HEALTH_SIGNALS - {"jobFailure"}
POD_HEALTH_RESOURCES = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Pod", "Job"}

# Public semantic categories supported for each exact Kubernetes kind.
CATEGORY_KINDS = {
    "image": WORKLOADS,
    "replicas": {"Deployment", "StatefulSet", "ReplicaSet"},
    "resources": WORKLOADS,
    "environment": WORKLOADS,
    "volumes": WORKLOADS,
    "configuration": {"ConfigMap"},
    "networking": {"Service", "Ingress"},
    "autoscaling": {"HorizontalPodAutoscaler"},
    "permissions": {"ClusterRole", "ClusterRoleBinding", "ServiceAccount"},
    "scheduling": {"Node"},
    "storage": {"PersistentVolume"},
    "labels": SUPPORTED_RESOURCES,
    "annotations": SUPPORTED_RESOURCES,
    "manifest": SUPPORTED_RESOURCES,
}
CATEGORIES = set(CATEGORY_KINDS)

# Robusta 0.48.0 applies these as substring checks. They intentionally admit a
# superset; semantic_match() is the exact package boundary.
COARSE_INCLUDE = {
    "image": ["image"],
    "replicas": ["replicas"],
    "resources": ["resources"],
    "environment": ["env"],
    "volumes": ["volumes", "volumeMounts"],
    "configuration": ["data", "binaryData"],
    "networking": ["spec"],
    "autoscaling": ["spec"],
    "permissions": ["rules", "roleRef", "subjects", "automountServiceAccountToken", "imagePullSecrets", "secrets"],
    "scheduling": ["taints", "unschedulable"],
    "storage": ["spec"],
    "labels": ["metadata.labels"],
    "annotations": ["metadata.annotations"],
    "manifest": ["spec", "data", "binaryData", "rules", "roleRef", "subjects", "automountServiceAccountToken", "imagePullSecrets", "secrets", "metadata.labels", "metadata.annotations", "type"],
}
NOISE_IGNORE = ["metadata.generation", "metadata.resourceVersion", "metadata.managedFields", "status"]
TRIGGER_KIND = {kind: re.sub(r"[^a-z0-9]", "", kind.lower()) for kind in SUPPORTED_RESOURCES}


def _require_keys(value, allowed, location):
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    extra = sorted(set(value) - set(allowed))
    if extra:
        raise ValueError(f"{location} has unknown fields: {', '.join(extra)}")


def _named_list(value, location, available):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{location} must be a non-empty list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{location} must contain strings")
    unknown = sorted(set(value) - set(available))
    if unknown:
        raise ValueError(f"{location} contains unsupported values: {', '.join(unknown)}")
    return list(dict.fromkeys(value))


def profile_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:35] or "profile"
    digest = hashlib.sha256(name.encode()).hexdigest()[:10]
    return f"{slug}-{digest}"


def _validate_destinations(config):
    destinations = config.get("destinations")
    if not isinstance(destinations, dict) or not destinations:
        raise ValueError("destinations must define at least one named destination")
    normalized = {}
    for name, definition in destinations.items():
        if not isinstance(name, str) or not CONFIG_NAME.fullmatch(name):
            raise ValueError(f"invalid destination name: {name}")
        _require_keys(definition, {"secretKey", "type"}, f"destination {name}")
        secret_key = definition.get("secretKey")
        destination_type = definition.get("type", "mattermost")
        if not isinstance(secret_key, str) or not CONFIG_NAME.fullmatch(secret_key):
            raise ValueError(f"destination {name} must contain a valid secretKey")
        if destination_type not in {"mattermost", "slack"}:
            raise ValueError(f"destination {name} type must be mattermost or slack")
        normalized[name] = {"type": destination_type, "secretKey": secret_key}
    defaults = _named_list(config.get("defaultDestinations"), "defaultDestinations", normalized)
    return normalized, defaults


def validate_v2(config):
    _require_keys(config, {"schemaVersion", "defaultDestinations", "destinations", "profiles"}, "profile configuration")
    if config.get("schemaVersion") != 1:
        raise ValueError("schemaVersion must be 1")
    destinations, defaults = _validate_destinations(config)
    profiles = config.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles must be a non-empty list")
    seen = set()
    normalized_profiles = []
    for index, profile in enumerate(profiles):
        location = f"profile[{index}]"
        _require_keys(profile, {"name", "enabled", "scope", "resources", "monitor", "context", "notify"}, location)
        name = profile.get("name")
        if not isinstance(name, str) or not CONFIG_NAME.fullmatch(name) or name in seen:
            raise ValueError(f"profile names must be valid and unique: {name}")
        seen.add(name)
        enabled = profile.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"profile {name} enabled must be boolean")

        scope = profile.get("scope", {})
        _require_keys(scope, {"namespaces", "labels"}, f"profile {name} scope")
        namespaces = scope.get("namespaces", [])
        if not isinstance(namespaces, list) or any(not isinstance(ns, str) or len(ns) > 63 or not DNS_LABEL.fullmatch(ns) for ns in namespaces):
            raise ValueError(f"profile {name} scope.namespaces must contain exact namespace names")
        namespaces = list(dict.fromkeys(namespaces))
        labels = scope.get("labels", {})
        if not isinstance(labels, dict) or any(not isinstance(k, str) or not LABEL_KEY.fullmatch(k) or not isinstance(v, str) or len(v) > 63 or not LABEL_VALUE.fullmatch(v) for k, v in labels.items()):
            raise ValueError(f"profile {name} scope.labels must contain exact Kubernetes label key/value pairs")

        resources = profile.get("resources")
        if not isinstance(resources, dict) or not resources:
            raise ValueError(f"profile {name} resources must map lowercase Kubernetes resource types to settings")
        normalized_resources = []
        seen_kinds = set()
        for resource_name, settings in resources.items():
            if not isinstance(resource_name, str):
                raise ValueError(f"profile {name} resource names must be strings")
            kind = RESOURCE_NAMES.get(resource_name.lower())
            if not kind:
                raise ValueError(f"profile {name} contains unsupported resource kind: {resource_name}")
            if kind in seen_kinds:
                raise ValueError(f"profile {name} declares {kind.lower()} more than once with different capitalization")
            if settings is None:
                settings = {}
            _require_keys(settings, {"names"}, f"profile {name} resource {resource_name}")
            names = settings.get("names", [])
            if not isinstance(names, list) or any(not isinstance(item, str) or len(item) > 253 or not DNS_SUBDOMAIN.fullmatch(item) for item in names):
                raise ValueError(f"profile {name} resource {resource_name}.names must contain exact resource names")
            seen_kinds.add(kind)
            normalized_resources.append({"kind": kind, "names": list(dict.fromkeys(names))})
        has_namespaced = bool(seen_kinds & NAMESPACED_RESOURCES)
        has_cluster = bool(seen_kinds & CLUSTER_RESOURCES)
        if has_namespaced and not namespaces:
            raise ValueError(f"profile {name} contains namespaced resources and requires scope.namespaces")
        if has_cluster and namespaces:
            raise ValueError(f"profile {name} contains cluster-scoped resources and cannot set scope.namespaces; use a separate profile")
        if has_namespaced and has_cluster:
            raise ValueError(f"profile {name} cannot mix namespaced and cluster-scoped resources")

        monitor = profile.get("monitor", {})
        _require_keys(monitor, {"changes", "drift", "health"}, f"profile {name} monitor")
        changes = monitor.get("changes", {})
        _require_keys(changes, {"events"}, f"profile {name} monitor.changes")
        events = changes.get("events", [])
        if events:
            events = _named_list(events, f"profile {name} monitor.changes.events", CHANGE_EVENTS)
        elif not isinstance(events, list):
            raise ValueError(f"profile {name} monitor.changes.events must be a list")

        drift = monitor.get("drift", {})
        _require_keys(drift, {"include"}, f"profile {name} monitor.drift")
        include = drift.get("include", [])
        if include:
            include = _named_list(include, f"profile {name} monitor.drift.include", CATEGORIES)
            for category in include:
                if not any(kind in CATEGORY_KINDS[category] for kind in seen_kinds):
                    raise ValueError(f"profile {name} drift category {category} does not apply to its resources")
            for kind in seen_kinds:
                if not any(kind in CATEGORY_KINDS[category] for category in include):
                    raise ValueError(f"profile {name} resource {kind} has no applicable drift category")
        elif not isinstance(include, list):
            raise ValueError(f"profile {name} monitor.drift.include must be a list")

        health = monitor.get("health", {})
        _require_keys(health, HEALTH_SIGNALS | {"defaults"}, f"profile {name} monitor.health")
        defaults_enabled = health.get("defaults", True)
        if not isinstance(defaults_enabled, bool) or any(signal in health and not isinstance(health[signal], bool) for signal in HEALTH_SIGNALS):
            raise ValueError(f"profile {name} health defaults and signal overrides must be boolean")
        has_pod_health_resource = bool(seen_kinds & POD_HEALTH_RESOURCES)
        normalized_health = {}
        for signal in sorted(HEALTH_SIGNALS):
            applicable = "Job" in seen_kinds if signal == "jobFailure" else has_pod_health_resource
            normalized_health[signal] = health.get(signal, defaults_enabled and applicable)
            if normalized_health[signal] and not applicable:
                requirement = "a job resource" if signal == "jobFailure" else "a workload, pod, or job resource"
                raise ValueError(f"profile {name} health.{signal} requires {requirement}")

        context = profile.get("context", {})
        _require_keys(context, {"diff", "kubernetesEvents"}, f"profile {name} context")
        if any(not isinstance(value, bool) for value in context.values()):
            raise ValueError(f"profile {name} context values must be boolean")

        notify = profile.get("notify", {})
        _require_keys(notify, {"severity", "destinations"}, f"profile {name} notify")
        severity = notify.get("severity", "high")
        if severity not in SEVERITIES:
            raise ValueError(f"profile {name} severity must be one of: {', '.join(sorted(SEVERITIES))}")
        selected_destinations = notify.get("destinations", defaults)
        selected_destinations = _named_list(selected_destinations, f"profile {name} destinations", destinations)
        if not events and not include and not any(normalized_health.values()) and enabled:
            raise ValueError(f"profile {name} must monitor changes, drift, or health")

        normalized_profiles.append({
            "id": profile_id(name), "name": name, "enabled": enabled,
            "scope": {"namespaces": namespaces, "labels": dict(sorted(labels.items()))},
            "resources": normalized_resources,
            "monitor": {"changes": {"events": events}, "drift": {"include": include}, "health": normalized_health},
            "context": {"diff": context.get("diff", True), "kubernetesEvents": context.get("kubernetesEvents", any(normalized_health.values()))},
            "notify": {"severity": severity, "destinations": selected_destinations},
        })
    return {"schemaVersion": 1, "defaultDestinations": defaults, "destinations": destinations, "profiles": normalized_profiles}


def legacy_to_v2(config):
    allowed = {"defaultSinks", "sinks", "namespacedAlertRules", "clusterAlertRules"}
    _require_keys(config, allowed, "legacy alert configuration")
    sinks = config.get("sinks", {})
    destinations = {}
    for name, definition in sinks.items():
        destinations[name] = {"type": definition.get("type", "mattermost"), "secretKey": definition.get("secretKey")}
    profiles = []
    for field, cluster in (("namespacedAlertRules", False), ("clusterAlertRules", True)):
        for rule in config.get(field, []):
            profile = {
                "name": rule.get("name"), "enabled": rule.get("enabled", True),
                "scope": {"namespaces": [] if cluster else rule.get("namespaces", [])},
                "resources": {kind.lower(): {} for kind in rule.get("resources", [])},
                "monitor": {"drift": {"include": ["manifest"]}, "health": {"defaults": False}},
                "context": {"diff": True},
                "notify": {"destinations": rule.get("sinks", config.get("defaultSinks", []))},
            }
            profiles.append(profile)
    return validate_v2({"schemaVersion": 1, "defaultDestinations": config.get("defaultSinks"), "destinations": destinations, "profiles": profiles})


def normalize_config(config):
    if not isinstance(config, dict):
        raise ValueError("profile configuration must be an object")
    return validate_v2(config) if "schemaVersion" in config else legacy_to_v2(config)


def coarse_includes(kind, categories):
    selected = []
    for category in categories:
        if kind not in CATEGORY_KINDS[category]:
            continue
        for value in COARSE_INCLUDE[category]:
            if value not in selected:
                selected.append(value)
    return selected


def semantic_category(kind, path):
    """Return the exact public category for one Robusta/Hikaru formatted path."""
    normalized = re.sub(r"\[(?:\d+|[^]]+)\]", "[]", path).lower()
    # Robusta's text webhook flattens Hikaru list indexes as `.0` while the
    # underlying DiffDetail paths and some tests render `[0]`.
    normalized = re.sub(r"\.\d+(?=\.|$)", "[]", normalized)
    if normalized.startswith("metadata.labels"):
        return "labels"
    if normalized.startswith("metadata.annotations"):
        return "annotations"
    if kind in WORKLOADS:
        if re.search(r"(?:^|\.)((?:init)?containers)\[\]\.image$", normalized): return "image"
        if normalized == "spec.replicas": return "replicas"
        if re.search(r"(?:^|\.)((?:init)?containers)\[\]\.resources(?:\.|$)", normalized): return "resources"
        if re.search(r"(?:^|\.)((?:init)?containers)\[\]\.(?:env|envfrom)(?:\.|\[|$)", normalized): return "environment"
        if ".volumes" in normalized or ".volumemounts" in normalized: return "volumes"
    if kind == "ConfigMap" and normalized.startswith(("data", "binarydata")): return "configuration"
    if kind in {"Service", "Ingress"} and normalized.startswith("spec"): return "networking"
    if kind == "HorizontalPodAutoscaler" and normalized.startswith("spec"): return "autoscaling"
    if kind == "ClusterRole" and normalized.startswith("rules"): return "permissions"
    if kind == "ClusterRoleBinding" and normalized.startswith(("roleref", "subjects")): return "permissions"
    if kind == "ServiceAccount" and normalized.startswith(("automountserviceaccounttoken", "imagepullsecrets", "secrets")): return "permissions"
    if kind == "Node" and normalized.startswith(("spec.taints", "spec.unschedulable")): return "scheduling"
    if kind == "PersistentVolume" and normalized.startswith("spec"): return "storage"
    return "manifest"


def matching_categories(kind, paths, selected):
    selected_set = set(selected)
    matches = []
    for path in paths:
        category = semantic_category(kind, path)
        if category in selected_set and category not in matches:
            matches.append(category)
        elif "manifest" in selected_set and "manifest" not in matches:
            matches.append("manifest")
    return matches


def canonical_json(config):
    return json.dumps(normalize_config(config), sort_keys=True, separators=(",", ":"))
