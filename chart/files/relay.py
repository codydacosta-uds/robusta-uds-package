# Copyright 2026 Defense Unicorns
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Defense-Unicorns-Commercial

import difflib
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent))
from profile_model import (  # noqa: E402
    CLUSTER_RESOURCES, NAMESPACED_RESOURCES, SUPPORTED_RESOURCES, WORKLOADS,
    matching_categories, normalize_config,
)

ENVIRONMENT = os.environ.get("ALERT_ENVIRONMENT", "production")
CONFIG_PATH = Path(os.environ.get("ALERT_CONFIG_PATH", "/app/profiles.json"))
SINK_DIRECTORY = Path(os.environ.get("SINK_DIRECTORY", "/etc/robusta-alert-webhooks"))
YELLOW = "#f2c744"
RED = "#d24b4b"
KIND_NAMES = {re.sub(r"[^a-z]", "", kind.lower()): kind for kind in SUPPORTED_RESOURCES}
PROFILE_MARKER = re.compile(r"^UDS_PROFILE_V2\|([^|]+)\|(change|drift|health)\|([^|]+)\|([^|]+)\|([^|]+)\|(.+)$")
DELIVERY_RETRY_DELAYS = (1, 2)


def value_diff(before, after):
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), n=3, lineterm="")
    rendered = "\n".join(line for line in lines if not line.startswith(("--- ", "+++ ")))
    rendered = rendered.replace("```", chr(39) * 3)
    return rendered[:3000] + ("\n... diff truncated" if len(rendered) > 3000 else "")


def display_kind(value):
    compact = re.sub(r"[^a-z]", "", value.lower())
    return KIND_NAMES.get(compact, value.replace("-", " ").title())


def parse_title(raw_title):
    marker = PROFILE_MARKER.fullmatch(raw_title)
    if marker:
        _, _, _, kind, namespace, name = marker.groups()
        return display_kind(kind), namespace or "unknown", name
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


def parse_profile_marker(raw_title):
    match = PROFILE_MARKER.fullmatch(raw_title)
    if not match:
        return None
    profile, signal, operation, kind, namespace, name = match.groups()
    return {"profile": profile, "signal": signal, "operation": operation, "kind": display_kind(kind), "namespace": namespace, "name": name}


def validate_config(config):
    return normalize_config(config)


def load_config():
    return validate_config(json.loads(CONFIG_PATH.read_text()))


def matching_sinks(kind, namespace, config, profile_id=None, name=None):
    config = config if config.get("schemaVersion") == 1 else validate_config(config)
    cluster_scoped = kind in CLUSTER_RESOURCES
    selected = []
    matched_profiles = []
    for profile in config["profiles"]:
        if not profile["enabled"] or (profile_id and profile["id"] != profile_id):
            continue
        resource = next((item for item in profile["resources"] if item["kind"] == kind), None)
        if not resource:
            continue
        if not cluster_scoped and namespace not in profile["scope"]["namespaces"]:
            continue
        if name and resource["names"] and name not in resource["names"]:
            continue
        matched_profiles.append(profile["name"])
        for destination in profile["notify"]["destinations"]:
            if destination not in selected:
                selected.append(destination)
    return selected, matched_profiles, cluster_scoped


def profile_by_id(config, identity):
    return next((profile for profile in config["profiles"] if profile["id"] == identity and profile["enabled"]), None)


def extract_changes(message):
    pattern = re.compile(r"(?ms)^\s*\*([^*]+)\*:\s*(.*?)\s*==>\s*(.*?)(?=\n\s*\*[^*]+\*:\s*|\Z)")
    return [(match.group(1).strip(), match.group(2).strip(), match.group(3).strip()) for match in pattern.finditer(message)]


def field_format(kind, path):
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


def render_alert(message, color=YELLOW, profiles=None, sink_type="mattermost", marker=None, categories=None, severity="high"):
    lines = message.splitlines()
    incoming_title = lines[0] if lines else "Kubernetes configuration changed"
    marker = marker or parse_profile_marker(incoming_title)
    kind, namespace, name = parse_title(incoming_title)
    operation = marker["operation"] if marker else "update"
    signal = marker["signal"] if marker else "change"
    if signal == "drift":
        raw_title = f"Configuration drift detected: {kind} {namespace}/{name}"
    elif operation == "create":
        raw_title = f"{kind} created: {namespace}/{name}"
    elif operation == "delete":
        raw_title = f"{kind} deleted: {namespace}/{name}"
    else:
        raw_title = f"{kind} changed: {namespace}/{name}"
    changes = extract_changes(message)
    if sink_type not in {"mattermost", "slack"}:
        raise ValueError(f"unsupported sink type: {sink_type}")
    bold = (lambda value: f"*{value}*") if sink_type == "slack" else (lambda value: f"**{value}**")
    code_fence = "```" if sink_type == "slack" else "```diff"
    rendered_changes = []
    for path, before, after in changes:
        label, shown_path = field_format(kind, path)
        if kind == "Secret" and path.lower().startswith(("data.", "stringdata.")):
            rendered_changes.append(f"{bold(f'{label}:')} {shown_path}\n_Value changed (redacted)._")
        else:
            rendered_changes.append(f"{bold(f'{label}:')} {shown_path}\n{code_fence}\n{value_diff(before, after)}\n```")
    if rendered_changes:
        change_text = "\n\n".join(rendered_changes)
        summary = f"{len(changes)} observed configuration {'difference' if len(changes) == 1 else 'differences'}"
    elif operation in {"create", "delete"}:
        change_text, summary = f"_{kind} {operation} observed._", f"Resource {operation}d"
    else:
        change_text, summary = "_Update detected; no field diff was available._", "Resource updated"
    scope = "Cluster" if color == RED else "Namespaced"
    fields = [
        {"title": "Environment", "value": ENVIRONMENT, "short": True},
        {"title": "Namespace", "value": namespace, "short": True},
        {"title": "Resource", "value": name, "short": True},
        {"title": "Kind", "value": kind, "short": True},
        {"title": "Scope", "value": scope, "short": True},
        {"title": "Severity", "value": severity.title(), "short": True},
    ]
    if profiles:
        fields.append({"title": "Profile", "value": ", ".join(profiles), "short": False})
    if categories:
        fields.append({"title": "Drift category", "value": ", ".join(categories), "short": False})
    return {"attachments": [{
        "fallback": raw_title, "color": color, "title": raw_title,
        "text": f"{bold('Summary:')} {summary}\n\n{bold(section_title(kind))}\n\n{change_text[:9000]}",
        "fields": fields, "footer": "Robusta Profile",
    }]}


def _safe_text(value, limit=1000):
    text = str(value)
    text = re.sub(r"(?i)(password|token|authorization|secret)(\s*[:=]\s*)\S+", r"\1\2[redacted]", text)
    return text.replace("```", "'''")[:limit]


def health_context(payload, sink_type="mattermost"):
    """Render bounded structured context; FileBlock/log contents are ignored."""
    sections = []
    bold = (lambda value: f"*{value}*") if sink_type == "slack" else (lambda value: f"**{value}**")
    for enrichment in payload.get("enrichments") or []:
        if not isinstance(enrichment, dict):
            continue
        title = _safe_text(enrichment.get("title") or "Health context", 120)
        details = []
        for block in enrichment.get("blocks") or []:
            if not isinstance(block, dict) or "contents" in block or "filename" in block:
                continue
            if isinstance(block.get("rows"), list):
                headers = [str(item) for item in block.get("headers") or []]
                for row in block["rows"][:6]:
                    cells = list(row) if isinstance(row, (list, tuple)) else [row]
                    if headers:
                        details.append(", ".join(f"{headers[index]}: {_safe_text(value, 240)}" for index, value in enumerate(cells[:len(headers)])))
                    else:
                        details.append(", ".join(_safe_text(value, 240) for value in cells))
            elif block.get("text"):
                details.append(_safe_text(block["text"], 1200))
            elif isinstance(block.get("items"), list):
                details.extend(_safe_text(item, 300) for item in block["items"][:6])
        if details:
            sections.append(f"{bold(title)}\n" + "\n".join(f"- {detail}" for detail in details))
    return "\n\n".join(sections)[:3500]


def render_health(payload, profile, marker, sink_type="mattermost"):
    if sink_type not in {"mattermost", "slack"}:
        raise ValueError(f"unsupported destination type: {sink_type}")
    bold = (lambda value: f"*{value}*") if sink_type == "slack" else (lambda value: f"**{value}**")
    subject = payload.get("subject") or {}
    kind = marker["kind"]
    namespace = marker["namespace"] or subject.get("namespace") or "unknown"
    name = marker["name"] or subject.get("name") or "unknown"
    signal_names = {"crashLoop": "Crash loop", "imagePullFailure": "Image pull failure", "oomKill": "Out-of-memory kill", "jobFailure": "Job failure", "podEviction": "Pod eviction"}
    signal = signal_names.get(marker["operation"], marker["operation"])
    description = _safe_text(payload.get("description") or "Kubernetes health failure detected.", 3000)
    context = health_context(payload, sink_type)
    fields = [
        {"title": "Environment", "value": ENVIRONMENT, "short": True},
        {"title": "Namespace", "value": namespace, "short": True},
        {"title": "Resource", "value": name, "short": True},
        {"title": "Kind", "value": kind, "short": True},
        {"title": "Health signal", "value": signal, "short": True},
        {"title": "Severity", "value": profile["notify"]["severity"].title(), "short": True},
        {"title": "Profile", "value": profile["name"], "short": False},
    ]
    title = f"{signal}: {kind} {namespace}/{name}"
    return {"attachments": [{
        "fallback": title, "color": RED, "title": title,
        "text": f"{bold('Summary:')} {description}" + (f"\n\n{context}" if context else ""), "fields": fields,
        "footer": "Robusta Profile",
    }]}


def sink_url(config, sink_name):
    config = config if config.get("schemaVersion") == 1 else validate_config(config)
    secret_key = config["destinations"][sink_name]["secretKey"]
    path = SINK_DIRECTORY / secret_key
    try:
        url = path.read_text().strip()
    except OSError as error:
        raise ValueError(f"destination {sink_name} secret key {secret_key} is unavailable") from error
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"destination {sink_name} does not contain a valid HTTP(S) URL")
    return url


def route_change(message, config):
    first_line = message.splitlines()[0] if message else ""
    marker = parse_profile_marker(first_line)
    kind, namespace, name = parse_title(first_line)
    if kind not in SUPPORTED_RESOURCES:
        raise ValueError(f"unsupported resource kind received: {kind}")
    profile = profile_by_id(config, marker["profile"]) if marker else None
    destinations, profiles, cluster_scoped = matching_sinks(kind, namespace, config, marker["profile"] if marker else None, name)
    categories = []
    if marker and marker["signal"] == "drift":
        if not profile:
            return [], None
        paths = [path for path, _, _ in extract_changes(message)]
        categories = matching_categories(kind, paths, profile["monitor"]["drift"]["include"])
        if not categories:
            return [], None
    severity = profile["notify"]["severity"] if profile else "high"
    rendered_message = message
    if profile and marker and marker["signal"] == "drift" and not profile["context"]["diff"]:
        rendered_message = first_line
    alerts = [(destination, render_alert(rendered_message, RED if cluster_scoped else YELLOW, profiles, config["destinations"][destination]["type"], marker, categories, severity)) for destination in destinations]
    return alerts, f"{kind} {namespace}/{name}"


def route_health(payload, config):
    marker = parse_profile_marker(str(payload.get("title", "")))
    if not marker or marker["signal"] != "health":
        return [], None
    profile = profile_by_id(config, marker["profile"])
    if not profile:
        return [], None
    alerts = [(destination, render_health(payload, profile, marker, config["destinations"][destination]["type"])) for destination in profile["notify"]["destinations"]]
    return alerts, f"{marker['kind']} {marker['namespace']}/{marker['name']}"


def deliver_alert(destination, alert, config):
    """Deliver once, retrying only transient request failures with bounded delays."""
    request = Request(
        sink_url(config, destination),
        data=json.dumps(alert).encode(),
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(len(DELIVERY_RETRY_DELAYS) + 1):
        try:
            with urlopen(request, timeout=10) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"destination {destination} returned {response.status}")
            return
        except Exception as error:
            if attempt == len(DELIVERY_RETRY_DELAYS):
                raise
            delay = DELIVERY_RETRY_DELAYS[attempt]
            print(
                f"Delivery attempt {attempt + 1} failed for [{destination}]: "
                f"{type(error).__name__}; retrying in {delay}s",
                flush=True,
            )
            time.sleep(delay)


class Relay(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace").strip()
        try:
            config = load_config()
            if self.path.rstrip("/") == "/health":
                alerts, subject = route_health(json.loads(body), config)
            else:
                alerts, subject = route_change(body, config)
            if not alerts:
                print(f"Dropped unmatched Profile event: {subject or 'unknown'}", flush=True)
                self.send_response(204)
                self.end_headers()
                return
            for destination, alert in alerts:
                deliver_alert(destination, alert, config)
                print(f"Delivered [{destination}]: {alert['attachments'][0]['fallback']}", flush=True)
            self.send_response(204)
        except Exception as error:
            print(f"Alert delivery failed: {error}", flush=True)
            self.send_response(502)
        self.end_headers()

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


def main():
    config = load_config()
    enabled = sum(1 for profile in config["profiles"] if profile["enabled"])
    print(f"Loaded {enabled} enabled Profile definitions", flush=True)
    HTTPServer(("0.0.0.0", 8080), Relay).serve_forever()


if __name__ == "__main__":
    main()
