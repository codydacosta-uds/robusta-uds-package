#!/usr/bin/env bash
set -euo pipefail
K=(uds zarf tools kubectl)
NS=zarf
GOOD_IMAGE=$("${K[@]}" -n robusta get deployment robusta-alert-relay -o jsonpath='{.spec.template.spec.containers[0].image}')
OWNED=(baseline-crash baseline-image baseline-oom baseline-evict baseline-stateful baseline-daemon baseline-replica baseline-job)
UNRELATED=(unrelated-crash unrelated-image unrelated-oom unrelated-evict unrelated-job optout-oom)
payload() { "${K[@]}" exec -n robusta deploy/robusta-webhook-mock -- python -c 'from urllib.request import urlopen; print(urlopen("http://127.0.0.1:8080/received", timeout=2).read().decode())' 2>/dev/null || true; }
count() {
  payload | python3 -c 'import json,sys
needle=sys.argv[1]
count=0
for line in sys.stdin:
    try: item=json.loads(line)
    except json.JSONDecodeError: continue
    attachments=item.get("attachments") or []
    title=str(attachments[0].get("fallback", "")) if attachments else ""
    if needle not in title or title.startswith("Configuration drift detected"):
        continue
    if needle == "optout-oom" and not title.startswith("Out-of-memory kill"):
        continue
    count += 1
print(count)' "$1"
}
cleanup() {
  "${K[@]}" -n "$NS" delete deployment baseline-crash baseline-image baseline-oom baseline-evict unrelated-crash unrelated-image unrelated-oom unrelated-evict optout-oom --ignore-not-found >/dev/null 2>&1 || true
  "${K[@]}" -n "$NS" delete statefulset baseline-stateful --ignore-not-found >/dev/null 2>&1 || true
  "${K[@]}" -n "$NS" delete daemonset baseline-daemon --ignore-not-found >/dev/null 2>&1 || true
  "${K[@]}" -n "$NS" delete replicaset baseline-replica --ignore-not-found >/dev/null 2>&1 || true
  "${K[@]}" -n "$NS" delete job baseline-job unrelated-job --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
# Earlier test phases intentionally retain webhook history. This phase owns the
# remaining assertions, so clear it to make positive and negative counts exact.
"${K[@]}" exec -n robusta deploy/robusta-webhook-mock -- python -c 'from pathlib import Path; Path("/data/payload.json").unlink(missing_ok=True)' >/dev/null
for name in "${OWNED[@]}" "${UNRELATED[@]}"; do
  key=${name//-/_}
  eval "before_${key}=$(count "$name")"
done

cat <<YAML | "${K[@]}" apply -f - >/dev/null
apiVersion: apps/v1
kind: Deployment
metadata: {name: baseline-crash, namespace: $NS}
spec:
  replicas: 1
  selector: {matchLabels: {test: baseline-crash}}
  template:
    metadata: {labels: {test: baseline-crash}}
    spec:
      containers:
        - name: app
          image: $GOOD_IMAGE
          command: ["python", "-c", "raise RuntimeError('intentional owned crash')"]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: unrelated-crash, namespace: $NS}
spec:
  replicas: 1
  selector: {matchLabels: {test: unrelated-crash}}
  template:
    metadata: {labels: {test: unrelated-crash}}
    spec:
      containers:
        - name: app
          image: $GOOD_IMAGE
          command: ["python", "-c", "raise RuntimeError('intentional unrelated crash')"]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: baseline-image, namespace: $NS}
spec:
  replicas: 1
  selector: {matchLabels: {test: baseline-image}}
  template:
    metadata: {labels: {test: baseline-image}}
    spec:
      containers:
        - name: app
          image: example.invalid/profile-baseline-does-not-exist:never
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: unrelated-image, namespace: $NS}
spec:
  replicas: 1
  selector: {matchLabels: {test: unrelated-image}}
  template:
    metadata: {labels: {test: unrelated-image}}
    spec:
      containers:
        - name: app
          image: example.invalid/profile-unrelated-does-not-exist:never
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: baseline-oom, namespace: $NS}
spec:
  replicas: 1
  selector: {matchLabels: {test: baseline-oom}}
  template:
    metadata: {labels: {test: baseline-oom}}
    spec:
      containers:
        - name: app
          image: $GOOD_IMAGE
          command: ["python", "-c", "x=bytearray(256*1024*1024); print(len(x))"]
          resources: {requests: {memory: 8Mi}, limits: {memory: 24Mi}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: unrelated-oom, namespace: $NS}
spec:
  replicas: 1
  selector: {matchLabels: {test: unrelated-oom}}
  template:
    metadata: {labels: {test: unrelated-oom}}
    spec:
      containers:
        - name: app
          image: $GOOD_IMAGE
          command: ["python", "-c", "x=bytearray(256*1024*1024); print(len(x))"]
          resources: {requests: {memory: 8Mi}, limits: {memory: 24Mi}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: optout-oom, namespace: $NS}
spec:
  replicas: 1
  selector: {matchLabels: {test: optout-oom}}
  template:
    metadata: {labels: {test: optout-oom}}
    spec:
      containers:
        - name: app
          image: $GOOD_IMAGE
          command: ["python", "-c", "x=bytearray(256*1024*1024); print(len(x))"]
          resources: {requests: {memory: 8Mi}, limits: {memory: 24Mi}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: baseline-evict, namespace: $NS}
spec:
  replicas: 1
  selector: {matchLabels: {test: baseline-evict}}
  template:
    metadata: {labels: {test: baseline-evict}}
    spec:
      containers:
        - name: app
          image: $GOOD_IMAGE
          command: ["python", "-c", "import time; time.sleep(600)"]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: unrelated-evict, namespace: $NS}
spec:
  replicas: 1
  selector: {matchLabels: {test: unrelated-evict}}
  template:
    metadata: {labels: {test: unrelated-evict}}
    spec:
      containers:
        - name: app
          image: $GOOD_IMAGE
          command: ["python", "-c", "import time; time.sleep(600)"]
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: baseline-stateful, namespace: $NS}
spec:
  serviceName: baseline-stateful
  replicas: 1
  selector: {matchLabels: {test: baseline-stateful}}
  template:
    metadata: {labels: {test: baseline-stateful}}
    spec:
      containers:
        - name: app
          image: $GOOD_IMAGE
          command: ["python", "-c", "raise RuntimeError('intentional owned stateful crash')"]
---
apiVersion: apps/v1
kind: DaemonSet
metadata: {name: baseline-daemon, namespace: $NS}
spec:
  selector: {matchLabels: {test: baseline-daemon}}
  template:
    metadata: {labels: {test: baseline-daemon}}
    spec:
      containers:
        - name: app
          image: $GOOD_IMAGE
          command: ["python", "-c", "raise RuntimeError('intentional owned daemon crash')"]
---
apiVersion: apps/v1
kind: ReplicaSet
metadata: {name: baseline-replica, namespace: $NS}
spec:
  replicas: 1
  selector: {matchLabels: {test: baseline-replica}}
  template:
    metadata: {labels: {test: baseline-replica}}
    spec:
      containers:
        - name: app
          image: $GOOD_IMAGE
          command: ["python", "-c", "raise RuntimeError('intentional owned replica crash')"]
---
apiVersion: batch/v1
kind: Job
metadata: {name: baseline-job, namespace: $NS}
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: job
          image: $GOOD_IMAGE
          command: ["python", "-c", "raise RuntimeError('intentional owned job failure')"]
---
apiVersion: batch/v1
kind: Job
metadata: {name: unrelated-job, namespace: $NS}
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: job
          image: $GOOD_IMAGE
          command: ["python", "-c", "raise RuntimeError('intentional unrelated job failure')"]
YAML

for deployment in baseline-evict unrelated-evict; do
  "${K[@]}" -n "$NS" rollout status deployment/$deployment --timeout=90s >/dev/null
  pod=$("${K[@]}" -n "$NS" get pod -l test=$deployment -o jsonpath='{.items[0].metadata.name}')
  "${K[@]}" -n "$NS" get pod "$pod" -o json | python3 -c 'import json,sys; x=json.load(sys.stdin); x["status"]["phase"]="Failed"; x["status"]["reason"]="Evicted"; x["status"]["message"]="Intentional ownership test eviction"; print(json.dumps(x))' >/tmp/profile-default-evicted.json
  "${K[@]}" replace --raw "/api/v1/namespaces/$NS/pods/$pod/status" -f /tmp/profile-default-evicted.json >/dev/null
done

for i in $(seq 1 180); do
  body=$(payload)
  ready=true
  for name in "${OWNED[@]}"; do
    key=${name//-/_}; before_var=before_${key}
    (( $(count "$name") > ${!before_var} )) || ready=false
  done
  if $ready; then
    for name in "${UNRELATED[@]}"; do
      key=${name//-/_}; before_var=before_${key}
      if (( $(count "$name") > ${!before_var} )); then
        echo "Unrelated or opted-out failure was delivered: $name"
        exit 1
      fi
    done

    crash_pod=$("${K[@]}" -n "$NS" get pod -l test=baseline-crash -o jsonpath='{.items[0].metadata.name}')
    before_repeat=$(count "$crash_pod")
    for value in 1 2 3; do
      "${K[@]}" -n "$NS" annotate pod "$crash_pod" profile-test-repeat=$value --overwrite >/dev/null
      sleep 2
    done
    sleep 5
    after_repeat=$(count "$crash_pod")
    if (( after_repeat != before_repeat )); then
      echo "Persistent CrashLoop produced a duplicate notification"
      exit 1
    fi
    echo "Implicit workload health, exact ownership negatives, OOM opt-out, and cooldown E2E passed"
    exit 0
  fi
  if (( i % 10 == 0 )); then
    echo "waiting for implicit health defaults (${i}/180)"
  fi
  sleep 2
done

echo "Timed out waiting for implicit Profile health defaults"
exit 1
