#!/usr/bin/env bash
# Copyright 2026 Defense Unicorns
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Defense-Unicorns-Commercial

set -euo pipefail

if [[ -x ./uds ]]; then
  uds_bin=./uds
elif command -v uds >/dev/null 2>&1; then
  uds_bin=$(command -v uds)
else
  echo "UDS CLI not found at ./uds or on PATH"
  exit 1
fi
kubectl=("$uds_bin" zarf tools kubectl)

wait_for_deployment() {
  local name="$1"
  "${kubectl[@]}" wait --for=condition=Available "deployment/${name}" -n robusta --timeout=180s
}

wait_for_deployment robusta-runner
wait_for_deployment robusta-forwarder
wait_for_deployment robusta-alert-relay
"${kubectl[@]}" rollout status deployment/robusta-runner -n robusta --timeout=180s
# The activation rollout can be complete while the old Pod is finishing its
# termination grace period. Do not treat that expected cleanup as a restart.
sleep 10

restart_counts() {
  # Ignore pods already terminating as part of a completed rollout.
  "${kubectl[@]}" get pods -n robusta -o go-template='{{range .items}}{{if not .metadata.deletionTimestamp}}{{.metadata.name}}{{" "}}{{range .status.containerStatuses}}{{.restartCount}}{{" "}}{{end}}{{"\n"}}{{end}}{{end}}'
}

# Allow startup retries while network policy mutations converge, then require stability.
baseline=$(restart_counts)
sleep 30
current=$(restart_counts)
if [[ "$baseline" != "$current" ]]; then
  echo "Robusta pods restarted after reaching readiness:"
  diff -u <(printf '%s\n' "$baseline") <(printf '%s\n' "$current") || true
  exit 1
fi

"${kubectl[@]}" get secret robusta-alert-webhooks -n robusta >/dev/null
"${kubectl[@]}" get deployment robusta-alert-relay -n robusta >/dev/null
"${kubectl[@]}" wait --for=condition=Available deployment/robusta-webhook-mock -n robusta --timeout=180s
[[ $("${kubectl[@]}" get deployment robusta-runner -n robusta -o jsonpath='{range .spec.template.spec.containers[*].env[?(@.name=="ENABLE_TELEMETRY")]}{.value}{end}') == "false" ]]

# The production playbook set and safe defaults must be present in the upstream package.
profiles=$("${kubectl[@]}" get configmap robusta-alert-relay -n robusta -o jsonpath='{.data.profiles\.json}')
grep -q 'semantic-drift-and-lifecycle' <<<"$profiles"
grep -q 'workload-health' <<<"$profiles"
playbooks=$("${kubectl[@]}" get secret robusta-playbooks-config-secret -n robusta -o jsonpath='{.data.active_playbooks\.yaml}' | base64 --decode)
[[ $(grep -c 'name: UdsProfileV2' <<<"$playbooks") -eq 35 ]]
[[ $(grep -c 'profile_ownership_filter:' <<<"$playbooks") -eq 11 ]]
"${kubectl[@]}" get configmap robusta-profile-actions -n robusta >/dev/null
[[ $(grep -c 'name: RuleBased' <<<"$playbooks") -eq 0 ]]
grep -q 'name: profile-change-relay' <<<"$playbooks"
grep -q 'name: profile-health-relay' <<<"$playbooks"
if "${kubectl[@]}" get clusterrole robusta-secret-watcher >/dev/null 2>&1; then
  echo "Secret watcher RBAC must not exist with default WATCH_SECRETS=false"
  exit 1
fi

# Cause a real legacy-translated Profile drift signal and verify typed delivery.
# The default exact namespace is zarf.
"${kubectl[@]}" create namespace zarf --dry-run=client -o yaml | "${kubectl[@]}" apply -f - >/dev/null
alert_name="robusta-alert-test-$$"
cleanup() {
  "${kubectl[@]}" delete configmap "$alert_name" -n zarf --ignore-not-found >/dev/null
}
trap cleanup EXIT
"${kubectl[@]}" create configmap "$alert_name" -n zarf --from-literal=status=before --dry-run=client -o yaml | "${kubectl[@]}" label -f - monitor=profile-v2 --local -o yaml | "${kubectl[@]}" apply -f - >/dev/null
# Ensure the add event is in the informer cache so the following update always
# has a before/after pair for resource_babysitter.
sleep 5
"${kubectl[@]}" patch configmap "$alert_name" -n zarf --type merge -p '{"data":{"status":"after"}}' >/dev/null

for _ in $(seq 1 60); do
  payload=$("${kubectl[@]}" exec deployment/robusta-webhook-mock -n robusta -- python -c 'from urllib.request import urlopen; print(urlopen("http://127.0.0.1:8080/received", timeout=2).read().decode())' 2>/dev/null || true)
  if python3 -c '
import json
import sys

name = sys.argv[1]
payloads = [json.loads(line) for line in sys.stdin if line.strip()]
texts = [
    item["attachments"][0]["text"]
    for item in payloads
    if name in item.get("attachments", [{}])[0].get("fallback", "")
]
assert any("**Summary:**" in text and "```diff" in text and "observed configuration" in text for text in texts)
assert any("*Summary:*" in text and "**Summary:**" not in text and "```diff" not in text and "observed configuration" in text for text in texts)
' "$alert_name" <<<"$payload" 2>/dev/null; then
    echo "Mattermost and Slack alert relay test passed"
    exit 0
  fi
  sleep 2
done

echo "Timed out waiting for the webhook mock to receive the Robusta alert"
exit 1
