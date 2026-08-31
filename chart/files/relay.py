# Copyright 2026 Defense Unicorns
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Defense-Unicorns-Commercial

import difflib
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ENVIRONMENT = os.environ.get("ALERT_ENVIRONMENT", "production")
CONFIG_PATH = Path(os.environ.get("ALERT_CONFIG_PATH", "/app/profiles.json"))
SINK_DIRECTORY = Path(os.environ.get("SINK_DIRECTORY", "/etc/robusta-webhooks"))
YELLOW = "#f2c744"
RED = "#d24b4b"

NAMESPACED_RESOURCES = {
    "ConfigMap", "DaemonSet", "Deployment", "HorizontalPodAutoscaler", "Ingress",
    "Job", "Pod", "ReplicaSet", "Secret", "Service", "ServiceAccount", "StatefulSet",
}
CLUSTER_RESOURCES = {"ClusterRole", "ClusterRoleBinding", "Namespace", "Node", "PersistentVolume"}
SUPPORTED_RESOURCES = NAMESPACED_RESOURCES | CLUSTER_RESOURCES
KIND_NAMES = {re.sub(r"[^a-z]", "", kind.lower()): kind for kind in SUPPORTED_RESOURCES}
WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Pod", "ReplicaSet", "Job"}
DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
CONFIG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def value_diff(before, after):
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), n=3, lineterm="")
    rendered = "\n".join(line for line in lines if not line.startswith(("--- ", "+++ ")))
    rendered = rendered.replace("```", chr(39) * 3)
    return rendered[:3000] + ("\n... diff truncated" if len(rendered) > 3000 else "")


def display_kind(value):
    compact = re.sub(r"[^a-z]", "", value.lower())
    return KIND_NAMES.get(compact, value.replace("-", " ").title())


def parse_title(raw_title):
    kind, separator, resource = raw_title.partition(" changed: ")
    if separator:
        namespace, _, name = resource.partition("/")
        return display_kind(kind), namespace or "unknown", name or resource
    native = re.match(r"^([^/]+)/([^/]+)/(.+?)(?:\.yaml)? updated$", raw_title, re.IGNORECASE)
    if native:
        kind, namespace, name = native.groups()
        return display_kind(kind), namespace or "cluster-scoped", name
    cluster_native = re.match(r"^([^/]+)/(.+?)(?:\.yaml)? updated$", raw_title, re.IGNORECASE)
    if cluster_native:
        kind, name = cluster_native.groups()
        return display_kind(kind), "cluster-scoped", name
    return "Kubernetes resource", "unknown", raw_title


def _validate_names(values, field, available):
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a non-empty list")
    unknown = sorted(set(values) - set(available))
    if unknown:
        raise ValueError(f"{field} contains unsupported values: {', '.join(unknown)}")


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("alert configuration must be an object")
    allowed = {"defaultSinks", "sinks", "alertProfiles", "clusterAlertProfiles"}
    unknown_fields = sorted(set(config) - allowed)
    if unknown_fields:
        raise ValueError(f"unknown alert configuration fields: {', '.join(unknown_fields)}")

    sinks = config.get("sinks")
    if not isinstance(sinks, dict) or not sinks:
        raise ValueError("sinks must define at least one named sink")
    for name, definition in sinks.items():
        if not CONFIG_NAME.fullmatch(name) or not isinstance(definition, dict):
            raise ValueError(f"invalid sink definition: {name}")
        if set(definition) != {"secretKey"} or not CONFIG_NAME.fullmatch(str(definition.get("secretKey", ""))):
            raise ValueError(f"sink {name} must contain only a valid secretKey")

    _validate_names(config.get("defaultSinks"), "defaultSinks", sinks)
    seen = set()
    for profile_type, resources, needs_namespaces in (
        ("alertProfiles", NAMESPACED_RESOURCES, True),
        ("clusterAlertProfiles", CLUSTER_RESOURCES, False),
    ):
        profiles = config.get(profile_type, [])
        if not isinstance(profiles, list):
            raise ValueError(f"{profile_type} must be a list")
        for profile in profiles:
            if not isinstance(profile, dict):
                raise ValueError(f"every {profile_type} entry must be an object")
            allowed_profile = {"name", "enabled", "resources", "sinks"} | ({"namespaces"} if needs_namespaces else set())
            extra = sorted(set(profile) - allowed_profile)
            if extra:
                raise ValueError(f"profile has unknown fields: {', '.join(extra)}")
            name = profile.get("name", "")
            if not isinstance(name, str) or not CONFIG_NAME.fullmatch(name) or name in seen:
                raise ValueError(f"profile names must be valid and unique: {name}")
            seen.add(name)
            if not isinstance(profile.get("enabled", True), bool):
                raise ValueError(f"profile {name} enabled must be boolean")
            _validate_names(profile.get("resources"), f"profile {name} resources", resources)
            if needs_namespaces:
                namespaces = profile.get("namespaces")
                if not isinstance(namespaces, list) or not namespaces:
                    raise ValueError(f"profile {name} namespaces must be a non-empty list")
                if any(not isinstance(ns, str) or not DNS_LABEL.fullmatch(ns) for ns in namespaces):
                    raise ValueError(f"profile {name} contains an invalid exact namespace")
            if "sinks" in profile:
                _validate_names(profile["sinks"], f"profile {name} sinks", sinks)
    return config


def load_config():
    return validate_config(json.loads(CONFIG_PATH.read_text()))


def matching_sinks(kind, namespace, config):
    cluster_scoped = kind in CLUSTER_RESOURCES
    profiles = config["clusterAlertProfiles"] if cluster_scoped else config["alertProfiles"]
    selected = []
    matched_profiles = []
    for profile in profiles:
        if not profile.get("enabled", True) or kind not in profile["resources"]:
            continue
        if not cluster_scoped and namespace not in profile["namespaces"]:
            continue
        matched_profiles.append(profile["name"])
        for sink in profile.get("sinks", config["defaultSinks"]):
            if sink not in selected:
                selected.append(sink)
    return selected, matched_profiles, cluster_scoped


def field_profile(kind, path):
    lower = path.lower()
    if kind == "ConfigMap" and lower.startswith(("data.", "binarydata.")):
        return "Data key", path.split(".", 1)[1]
    if kind == "Secret":
        if lower.startswith(("data.", "stringdata.")):
            return "Secret data", path.split(".", 1)[1]
        if "metadata.labels" in lower: return "Secret label", path
        if "metadata.annotations" in lower: return "Secret annotation", path
        if lower == "type": return "Secret type", path
    if kind in WORKLOADS:
        if lower.endswith(".image") or ".image." in lower: return "Container image", path
        if "replicas" in lower: return "Replica count", path
        if ".env" in lower: return "Container environment", path
        if ".resources" in lower: return "Container resources", path
        if "volume" in lower: return "Volume", path
        if "template" in lower: return "Pod template field", path
        return "Workload field", path
    if kind == "Service":
        if "ports" in lower: return "Service port", path
        if "selector" in lower: return "Selector", path
        if lower.endswith("type"): return "Service type", path
    if kind == "Ingress":
        for token, label in (("host", "Host"), ("path", "Route path"), ("tls", "TLS configuration"), ("backend", "Backend")):
            if token in lower: return label, path
    if kind == "HorizontalPodAutoscaler":
        for token, label in (("minreplicas", "Minimum replicas"), ("maxreplicas", "Maximum replicas"), ("metrics", "Scaling metric"), ("scaletargetref", "Scale target")):
            if token in lower: return label, path
    if kind == "PersistentVolume":
        for token, label in (("storageclassname", "Storage class"), ("accessmodes", "Access mode"), ("capacity", "Capacity"), ("volume", "Volume source")):
            if token in lower: return label, path
    if kind in {"ClusterRole", "ClusterRoleBinding"}:
        for token, label in (("roleref", "Role reference"), ("subjects", "Subject"), ("rules", "RBAC rule")):
            if token in lower: return label, path
    if kind == "Node":
        for token, label in (("taints", "Taint"), ("unschedulable", "Scheduling"), ("labels", "Node label"), ("annotations", "Node annotation")):
            if token in lower: return label, path
    if kind == "ServiceAccount":
        for token, label in (("imagepullsecrets", "Image pull secret"), ("automount", "Token automount"), ("secrets", "Service account secret")):
            if token in lower: return label, path
    if kind == "Namespace" and "labels" in lower: return "Namespace label", path
    if kind == "Namespace" and "annotations" in lower: return "Namespace annotation", path
    return "Field", path


def section_title(kind):
    if kind == "ConfigMap": return "Configuration data changes"
    if kind == "Secret": return "Secret changes"
    if kind in WORKLOADS: return "Workload manifest changes"
    if kind in {"Service", "Ingress"}: return "Network configuration changes"
    if kind == "HorizontalPodAutoscaler": return "Autoscaling changes"
    if kind == "PersistentVolume": return "Storage configuration changes"
    if kind in {"ClusterRole", "ClusterRoleBinding", "ServiceAccount"}: return "Access-control changes"
    return "Manifest changes"


def render_alert(message, color=YELLOW, profiles=None):
    lines = message.splitlines()
    incoming_title = lines[0] if lines else "Kubernetes configuration changed"
    kind, namespace, name = parse_title(incoming_title)
    raw_title = f"{kind} changed: {namespace}/{name}"
    pattern = re.compile(r"(?ms)^\s*\*([^*]+)\*:\s*(.*?)\s*==>\s*(.*?)(?=\n\s*\*[^*]+\*:\s*|\Z)")
    changes = [(m.group(1).strip(), m.group(2).strip(), m.group(3).strip()) for m in pattern.finditer(message)]
    rendered_changes = []
    for path, before, after in changes:
        label, shown_path = field_profile(kind, path)
        if kind == "Secret" and path.lower().startswith(("data.", "stringdata.")):
            rendered_changes.append(f"**{label}:** {shown_path}\n_Value changed (redacted)._")
        else:
            rendered_changes.append(f"**{label}:** {shown_path}\n```diff\n{value_diff(before, after)}\n```")
    if rendered_changes:
        change_text = "\n\n".join(rendered_changes)
        summary = f"{len(changes)} manifest {'change' if len(changes) == 1 else 'changes'}"
    else:
        change_text, summary = "_Update detected; no field diff was available._", "Resource updated"
    scope = "Cluster" if color == RED else "Namespaced"
    fields = [
        {"title": "Environment", "value": ENVIRONMENT, "short": True},
        {"title": "Namespace", "value": namespace, "short": True},
        {"title": "Resource", "value": name, "short": True},
        {"title": "Kind", "value": kind, "short": True},
        {"title": "Scope", "value": scope, "short": True},
        {"title": "Severity", "value": "Warning", "short": True},
    ]
    if profiles:
        fields.append({"title": "Alert profile", "value": ", ".join(profiles), "short": False})
    return {"attachments": [{
        "fallback": raw_title, "color": color, "title": f"⚠️ {raw_title}",
        "text": f"**Summary:** {summary}\n\n**{section_title(kind)}**\n\n{change_text[:9000]}\n\n_Actor and API request: EKS audit logs_",
        "fields": fields, "footer": "Robusta",
    }]}


def sink_url(config, sink_name):
    secret_key = config["sinks"][sink_name]["secretKey"]
    path = SINK_DIRECTORY / secret_key
    try:
        url = path.read_text().strip()
    except OSError as error:
        raise ValueError(f"sink {sink_name} secret key {secret_key} is unavailable") from error
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"sink {sink_name} does not contain a valid HTTP(S) URL")
    return url


class Relay(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        message = self.rfile.read(length).decode("utf-8", errors="replace").strip()
        try:
            config = load_config()
            kind, namespace, _ = parse_title(message.splitlines()[0] if message else "")
            if kind not in SUPPORTED_RESOURCES:
                raise ValueError(f"unsupported resource kind received: {kind}")
            sinks, profiles, cluster_scoped = matching_sinks(kind, namespace, config)
            if not sinks:
                print(f"Dropped unmatched event: {kind} {namespace}", flush=True)
                self.send_response(204)
                self.end_headers()
                return
            alert = render_alert(message, RED if cluster_scoped else YELLOW, profiles)
            for sink in sinks:
                request = Request(sink_url(config, sink), data=json.dumps(alert).encode(), headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=10) as response:
                    if not 200 <= response.status < 300:
                        raise RuntimeError(f"sink {sink} returned {response.status}")
                print(f"Delivered [{sink}]: {alert['attachments'][0]['fallback']}", flush=True)
            self.send_response(204)
        except Exception as error:
            print(f"Mattermost delivery failed: {error}", flush=True)
            self.send_response(502)
        self.end_headers()

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


def main():
    config = load_config()
    print(f"Loaded {len(config['alertProfiles'])} namespaced and {len(config['clusterAlertProfiles'])} cluster alert profiles", flush=True)
    HTTPServer(("0.0.0.0", 8080), Relay).serve_forever()


if __name__ == "__main__":
    main()
