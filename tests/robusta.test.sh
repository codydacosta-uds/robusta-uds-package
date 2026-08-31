#!/usr/bin/env bash
# Copyright 2026 Defense Unicorns
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Defense-Unicorns-Commercial

set -euo pipefail

kubectl=(./uds zarf tools kubectl)

wait_for_deployment() {
  local name="$1"
  "${kubectl[@]}" wait --for=condition=Available "deployment/${name}" -n robusta --timeout=180s
}

wait_for_deployment robusta-runner
wait_for_deployment robusta-forwarder
wait_for_deployment robusta-mattermost-relay

restart_counts() {
  "${kubectl[@]}" get pods -n robusta -o jsonpath='{range .items[*]}{.metadata.name}{" "}{range .status.containerStatuses[*]}{.restartCount}{" "}{end}{"\n"}{end}'
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

"${kubectl[@]}" get secret robusta-mattermost-webhook -n robusta >/dev/null
"${kubectl[@]}" get deployment robusta-mattermost-relay -n robusta >/dev/null
"${kubectl[@]}" wait --for=condition=Available deployment/robusta-mattermost-mock -n robusta --timeout=180s

# Cause a real Robusta change alert and verify the relay delivered a Mattermost-shaped payload.
"${kubectl[@]}" create namespace robusta-test --dry-run=client -o yaml | "${kubectl[@]}" apply -f - >/dev/null
alert_name="robusta-alert-test-$$"
"${kubectl[@]}" create configmap "$alert_name" -n robusta-test --from-literal=status=before --dry-run=client -o yaml | "${kubectl[@]}" apply -f - >/dev/null
"${kubectl[@]}" patch configmap "$alert_name" -n robusta-test --type merge -p '{"data":{"status":"after"}}' >/dev/null

for _ in $(seq 1 60); do
  payload=$("${kubectl[@]}" exec deployment/robusta-mattermost-mock -n robusta -- python -c 'from urllib.request import urlopen; print(urlopen("http://127.0.0.1:8080/received", timeout=2).read().decode())' 2>/dev/null || true)
  if grep -q "$alert_name" <<<"$payload" && grep -q 'ConfigMap changed' <<<"$payload" && grep -q 'Environment' <<<"$payload" && grep -q 'attachments' <<<"$payload"; then
    echo "Mattermost relay test passed"
    exit 0
  fi
  sleep 2
done

echo "Timed out waiting for the Mattermost mock to receive the Robusta alert"
exit 1
