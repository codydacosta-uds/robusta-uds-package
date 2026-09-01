#!/usr/bin/env bash
set -euo pipefail
K=(uds zarf tools kubectl)
NS=zarf
CM=semantic-config
DEP=semantic-workload
NEG=semantic-negative
ROLE=profile-v2-role
payload() { "${K[@]}" exec -n robusta deploy/robusta-webhook-mock -- python -c 'from urllib.request import urlopen; print(urlopen("http://127.0.0.1:8080/received", timeout=2).read().decode())' 2>/dev/null || true; }
count() { payload | grep -c "$1" || true; }
wait_count() {
  local needle=$1 minimum=$2
  for _ in $(seq 1 45); do
    local current; current=$(count "$needle")
    if (( current >= minimum )); then echo "$needle count=$current"; return 0; fi
    sleep 2
  done
  echo "timed out waiting for $needle >= $minimum"; return 1
}
cleanup() {
  "${K[@]}" -n "$NS" delete deployment "$DEP" --ignore-not-found >/dev/null || true
  "${K[@]}" -n "$NS" delete configmap "$CM" "$NEG" --ignore-not-found >/dev/null || true
  "${K[@]}" delete clusterrole "$ROLE" --ignore-not-found >/dev/null || true
}
trap cleanup EXIT
cleanup
"${K[@]}" create ns "$NS" --dry-run=client -o yaml | "${K[@]}" apply -f - >/dev/null

cm0=$(count "$CM")
"${K[@]}" -n "$NS" create configmap "$CM" --from-literal=status=before --dry-run=client -o yaml | "${K[@]}" label -f - monitor=profile-v2 --local -o yaml | "${K[@]}" apply -f - >/dev/null
wait_count "$CM" $((cm0 + 2)) # Mattermost + Slack create
sleep 3
"${K[@]}" -n "$NS" patch configmap "$CM" --type=merge -p '{"data":{"status":"after"}}' >/dev/null
wait_count "$CM" $((cm0 + 4)) # drift
"${K[@]}" -n "$NS" delete configmap "$CM" >/dev/null
wait_count "$CM" $((cm0 + 6)) # delete

neg0=$(count "$NEG")
"${K[@]}" -n "$NS" create configmap "$NEG" --from-literal=status=before --dry-run=client -o yaml | "${K[@]}" label -f - monitor=wrong --local -o yaml | "${K[@]}" apply -f - >/dev/null
sleep 8
[[ $(count "$NEG") -eq "$neg0" ]]
"${K[@]}" -n "$NS" delete configmap "$NEG" >/dev/null

dep0=$(count "$DEP")
cat <<YAML | "${K[@]}" apply -f - >/dev/null
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $DEP
  namespace: $NS
  labels: {monitor: profile-v2}
spec:
  replicas: 0
  selector: {matchLabels: {app: $DEP}}
  template:
    metadata: {labels: {app: $DEP}}
    spec:
      containers:
        - name: app
          image: example.invalid/app:v1
YAML
wait_count "$DEP" $((dep0 + 2))
sleep 3
"${K[@]}" -n "$NS" set image deployment/$DEP app=example.invalid/app:v2 >/dev/null
wait_count "$DEP" $((dep0 + 4))
policy_count=$(count "$DEP")
"${K[@]}" -n "$NS" patch deployment "$DEP" --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Always"}]' >/dev/null
sleep 10
[[ $(count "$DEP") -eq "$policy_count" ]]
"${K[@]}" -n "$NS" delete deployment "$DEP" >/dev/null
wait_count "$DEP" $((dep0 + 6))

role0=$(count "$ROLE")
"${K[@]}" create clusterrole "$ROLE" --verb=get --resource=configmaps >/dev/null
wait_count "$ROLE" $((role0 + 2))
"${K[@]}" patch clusterrole "$ROLE" --type=json -p='[{"op":"add","path":"/rules/0/verbs/-","value":"list"}]' >/dev/null
wait_count "$ROLE" $((role0 + 4))
"${K[@]}" delete clusterrole "$ROLE" >/dev/null
wait_count "$ROLE" $((role0 + 6))

echo "Profile v2 create/update/delete, exact scope, semantic drift, and cluster-scope E2E passed"
