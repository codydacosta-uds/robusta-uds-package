#!/usr/bin/env bash
# Copyright 2026 Defense Unicorns
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Defense-Unicorns-Commercial

set -euo pipefail

kubectl=(./uds zarf tools kubectl)

wait_for_deployment() {
  local name="$1"
  "${kubectl[@]}" wait --for=condition=Available "deployment/${name}" -n robusta --timeout=180s
}

wait_for_deployment robusta
wait_for_deployment robusta-mattermost-relay

restarts=$("${kubectl[@]}" get pods -n robusta -o jsonpath='{range .items[*]}{.metadata.name}{" "}{range .status.containerStatuses[*]}{.restartCount}{" "}{end}{"\n"}{end}')
if grep -E '[[:space:]][1-9][0-9]*([[:space:]]|$)' <<<"$restarts"; then
  echo "Robusta pods restarted unexpectedly:"
  echo "$restarts"
  exit 1
fi

"${kubectl[@]}" get secret robusta-mattermost-webhook -n robusta >/dev/null
"${kubectl[@]}" get deployment robusta-mattermost-relay -n robusta >/dev/null
