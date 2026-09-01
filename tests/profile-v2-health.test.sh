#!/usr/bin/env bash
set -euo pipefail
K=(uds zarf tools kubectl)
NS=zarf
CRASH=health-crashloop
IMAGE=health-imagepull
OOM=health-oom
EVICT=health-evicted
JOB=health-job
GOOD_IMAGE=$("${K[@]}" -n robusta get deployment robusta-alert-relay -o jsonpath='{.spec.template.spec.containers[0].image}')
payload() { "${K[@]}" exec -n robusta deploy/robusta-webhook-mock -- python -c 'from urllib.request import urlopen; print(urlopen("http://127.0.0.1:8080/received", timeout=2).read().decode())' 2>/dev/null || true; }
count() { payload | grep -c "$1" || true; }
cleanup() {
  "${K[@]}" -n "$NS" delete pod "$CRASH" "$IMAGE" "$OOM" "$EVICT" --force --grace-period=0 --ignore-not-found >/dev/null 2>&1 || true
  "${K[@]}" -n "$NS" delete job "$JOB" --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
for name in "$CRASH" "$IMAGE" "$OOM" "$EVICT" "$JOB"; do eval "before_${name//-/_}=$(count "$name")"; done
cat <<YAML | "${K[@]}" apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata: {name: $CRASH, namespace: $NS, labels: {monitor: profile-v2-health}}
spec:
  containers:
    - name: app
      image: $GOOD_IMAGE
      command: ["python", "-c", "raise RuntimeError('intentional profile test')"]
---
apiVersion: v1
kind: Pod
metadata: {name: $IMAGE, namespace: $NS, labels: {monitor: profile-v2-health}}
spec:
  containers:
    - name: app
      image: example.invalid/profile-v2-does-not-exist:never
---
apiVersion: v1
kind: Pod
metadata: {name: $OOM, namespace: $NS, labels: {monitor: profile-v2-health}}
spec:
  restartPolicy: Never
  containers:
    - name: app
      image: $GOOD_IMAGE
      command: ["python", "-c", "x=bytearray(256*1024*1024); print(len(x))"]
      resources:
        limits: {memory: 24Mi}
        requests: {memory: 8Mi}
---
apiVersion: v1
kind: Pod
metadata: {name: $EVICT, namespace: $NS, labels: {monitor: profile-v2-health}}
spec:
  restartPolicy: Never
  containers:
    - name: app
      image: $GOOD_IMAGE
      command: ["python", "-c", "import time; time.sleep(600)"]
---
apiVersion: batch/v1
kind: Job
metadata: {name: $JOB, namespace: $NS, labels: {monitor: profile-v2-health}}
spec:
  backoffLimit: 0
  template:
    metadata: {labels: {monitor: profile-v2-health}}
    spec:
      restartPolicy: Never
      containers:
        - name: job
          image: $GOOD_IMAGE
          command: ["python", "-c", "raise RuntimeError('intentional job failure')"]
YAML
"${K[@]}" -n "$NS" wait --for=condition=Ready pod/$EVICT --timeout=90s >/dev/null
"${K[@]}" -n "$NS" get pod "$EVICT" -o json | python3 -c 'import json,sys; x=json.load(sys.stdin); x["status"]["phase"]="Failed"; x["status"]["reason"]="Evicted"; x["status"]["message"]="Intentional Profile v2 eviction test"; print(json.dumps(x))' >/tmp/profile-evicted.json
"${K[@]}" replace --raw "/api/v1/namespaces/$NS/pods/$EVICT/status" -f /tmp/profile-evicted.json >/dev/null

for i in $(seq 1 180); do
  body=$(payload)
  crash=$(grep -c "$CRASH" <<<"$body" || true)
  image=$(grep -c "$IMAGE" <<<"$body" || true)
  oom=$(grep -c "$OOM" <<<"$body" || true)
  evict=$(grep -c "$EVICT" <<<"$body" || true)
  job=$(grep -c "$JOB" <<<"$body" || true)
  if (( crash > before_health_crashloop && image > before_health_imagepull && oom > before_health_oom && evict > before_health_evicted && job > before_health_job )); then
    job_payloads=$(grep "$JOB" <<<"$body")
    grep -q "Job Events" <<<"$job_payloads"
    if grep -q "intentional job failure" <<<"$job_payloads"; then
      echo "Application log content must not be delivered"
      exit 1
    fi
    echo "health deliveries crash=$crash image=$image oom=$oom eviction=$evict job=$job"
    echo "Profile v2 five-signal health and safe Event context E2E passed"
    exit 0
  fi
  if (( i % 10 == 0 )); then
    echo "waiting crash=$crash image=$image oom=$oom eviction=$evict job=$job"
    "${K[@]}" -n "$NS" get pod "$CRASH" "$IMAGE" "$OOM" "$EVICT" -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,REASON:.status.reason,WAITING:.status.containerStatuses[0].state.waiting.reason,TERMINATED:.status.containerStatuses[0].state.terminated.reason,RESTARTS:.status.containerStatuses[0].restartCount --no-headers 2>/dev/null || true
  fi
  sleep 2
done
echo "Timed out waiting for all Profile v2 health signals"
exit 1
