# UDS Robusta Package

This package gives Kubernetes applications a low-noise operational safety net. A Profile describes the resources that make up an application; the package automatically monitors applicable workloads for common health failures and sends concise notifications to Mattermost or Slack. No custom Robusta configuration is required.

A Robusta SaaS account, UI, Prometheus stack, or AI service is not required.

## Quick start

### Prerequisites

- A Kubernetes cluster with UDS Core installed.
- A Mattermost incoming webhook reachable over HTTPS for the default quick start. Profiles can instead configure Slack.
- A released `zarf-package-robusta-...-upstream.tar.zst` package.

### 1. Create the external webhook Secret

Webhook URLs are never stored in Profile configuration:

```bash
kubectl create namespace robusta --dry-run=client -o yaml | kubectl apply -f -

kubectl -n robusta create secret generic robusta-alert-webhooks \
  --from-literal=alerts-default-url='https://mattermost.example/hooks/REPLACE_ME' \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 2. Deploy

The default configuration provides workload-health monitoring for UDS Core workloads in the exact `zarf` namespace. It does not notify on routine manifest changes:

```bash
PACKAGE=zarf-package-robusta-amd64-<version>-upstream.tar.zst

uds zarf package deploy "$PACKAGE" \
  --set-variables CLUSTER_NAME=my-cluster \
  --set-variables ALERT_ENVIRONMENT=development \
  --confirm
```

### 3. Verify

```bash
kubectl -n robusta get package.uds.dev robusta
kubectl -n robusta get deployments
```

The Package should report `Ready`, with these deployments available:

- `robusta-runner`
- `robusta-forwarder`
- `robusta-alert-relay`

### 4. Send a controlled health test

Use the already-packaged relay image so the test does not require an external registry:

```bash
IMAGE=$(kubectl -n robusta get deployment robusta-alert-relay \
  -o jsonpath='{.spec.template.spec.containers[0].image}')

cat <<YAML | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: robusta-getting-started
  namespace: zarf
spec:
  selector:
    matchLabels:
      app: robusta-getting-started
  template:
    metadata:
      labels:
        app: robusta-getting-started
    spec:
      containers:
        - name: test
          image: ${IMAGE}
          command: ["python", "-c", "raise RuntimeError('intentional quick-start test')"]
YAML

# Remove the controlled failure after its CrashLoop notification arrives.
kubectl -n zarf delete deployment robusta-getting-started
```

The Deployment receives automatic crash-loop monitoring through the built-in UDS Core Profile. Robusta waits for at least two restarts before notifying.

## Built-in monitoring

No Profile configuration is required to start. The package includes one deliberately narrow Profile:

- **`uds-core-health`** monitors Deployments, StatefulSets, DaemonSets, ReplicaSets, and Jobs in the exact `zarf` namespace for applicable baseline health failures.

It sends to the `alerts-default` Mattermost destination, backed by the `alerts-default-url` key in `robusta-alert-webhooks`. It does not enable manifest drift, create/delete notifications, cluster-resource monitoring, standalone Pod monitoring, or Secret watching. Those behaviors require an intentional Profile so a new installation does not produce routine infrastructure noise.

Supplying `PROFILE_CONFIG` replaces the built-in Profile with the exact application boundaries and optional destinations you define. Include an equivalent `uds-core-health` Profile in custom configuration when that coverage should remain; custom and built-in Profiles are not silently merged.

## Core concepts

| Concept | Meaning |
|---|---|
| **Profile** | Defines an application or operational boundary and enrolls applicable workloads in baseline health monitoring. |
| **Scope** | Exact namespaces and labels that identify monitored objects. Resource names are configured under each resource type. |
| **Drift** | A relevant live update from the previously observed in-cluster resource state. |
| **Lifecycle change** | Creation or deletion of a matching resource. |
| **Health signal** | A crash loop, image-pull failure, OOM kill, failed Job, or Pod eviction. |
| **Destination** | A logical Mattermost or Slack target whose URL is stored in the external Secret. |
| **Context** | Safe supporting details such as old/new diffs and Kubernetes Events. |

## GitLab Profile example

A GitLab owner only describes which Kubernetes resources make up GitLab:

```yaml
schemaVersion: 1

profiles:
  - name: gitlab
    scope:
      namespaces:
        - gitlab
    resources:
      deployment:
        names:
          - gitlab-webservice
          - gitlab-sidekiq
      statefulset:
        names:
          - gitaly
```

That is enough to automatically monitor Pods owned by the selected GitLab Deployments and StatefulSet for crash loops, image-pull failures, OOM kills, and eviction. The user does not configure Robusta triggers, playbooks, ownership traversal, cooldowns, or the standard Mattermost destination. Resource names should be adjusted to the exact names produced by the installed GitLab package or Helm release.

Start from [`examples/profile-config.yaml`](examples/profile-config.yaml) and supply the YAML directly through `PROFILE_CONFIG`:

```bash
PROFILE_CONFIG="$(cat examples/profile-config.yaml)"

uds zarf package deploy "$PACKAGE" \
  --set-variables CLUSTER_NAME=my-cluster \
  --set-variables ALERT_ENVIRONMENT=development \
  --set-variables "PROFILE_CONFIG=${PROFILE_CONFIG}" \
  --confirm
```

When `destinations` is omitted, the package uses the standard `alerts-default` Mattermost destination and reads its URL from `alerts-default-url` in the external Secret. Users define destination objects only when they need a different or additional destination.

A complete UDS configuration example is available at [`examples/uds-config.yaml`](examples/uds-config.yaml).

Resource types are written in lowercase for readability. Matching is case-insensitive, so `deployment`, `Deployment`, and `DEPLOYMENT` resolve to the same Kubernetes type. `{}` selects every matching resource of that type; `names` selects exact objects only.

Routine GitLab deployments and configuration updates are not notified unless the Profile explicitly configures lifecycle events or semantic drift.

## Drift semantics

Profile drift monitoring catches qualifying live resource updates, including changes made by:

- manual Kubernetes commands;
- deployment tools;
- package or bundle releases;
- operators and controllers;
- other Kubernetes API clients.

Each notification shows the observed old/new fields and semantic category.

Profile drift monitoring does not compare the cluster against Git, Terraform, or Helm source and does not identify the actor that made a change. It therefore reports **observed in-cluster drift**, not desired-state reconciliation or authorization conclusions.

## Supported drift categories

- Container images
- Replica counts
- Resource requests and limits
- Environment variables and sources
- Volumes and mounts
- ConfigMap configuration
- Service and Ingress networking
- HPA autoscaling
- RBAC and service-account permissions
- Node scheduling
- PersistentVolume storage
- Labels and annotations
- Broad manifest field monitoring

The package performs exact final category classification. Selecting `image`, for example, alerts on a container image change but not an `imagePullPolicy` change.

## Automatic health monitoring

Creating a Profile automatically enables applicable baseline health monitoring:

| Selected resource | Automatic health signals |
|---|---|
| `deployment`, `statefulset`, `daemonset`, `replicaset`, `pod` | Crash loop, image-pull failure, OOM kill, Pod eviction |
| `job` | The Pod signals above plus Job failure |
| Configuration, networking, storage, and RBAC resources | No workload-health signals |

The package resolves Pod controller ownership, including `Pod → ReplicaSet → Deployment`, before routing health findings. A failure from an unrelated workload in the same namespace is suppressed. Profile labels are checked against the selected workload rather than assumed to exist on generated Pods.

Defaults are overridden only when needed:

```yaml
monitor:
  health:
    oomKill: false
    podEviction: false
```

Set `defaults: false` to disable the entire baseline. Individual signals can then be enabled explicitly.

Unscoped upstream health notifications are disabled. Safe Kubernetes Event context is enabled automatically for health findings. Application logs, Prometheus graphs, and node command output are not attached.

Existing Robusta controls reduce repeated noise: crash-loop, image-pull, and eviction findings have four-hour rate limits; OOM findings have a one-hour rate limit; Job failure fires only on transition to Failed. A four-hour cooldown means the same qualifying problem for the same runtime identity will not notify again during that period, even if it briefly recovers and returns. These cooldowns are runner-memory controls, not durable incident state, and reset when the runner restarts. Image-pull findings also wait at least 120 seconds to confirm the failure persists.

For transient destination failures, the package relay makes up to three delivery attempts with one- and two-second delays. Retries are bounded and held only for the current notification; they are not a persistent delivery queue.

## Alert examples

**Namespaced configuration drift**

![Namespaced Kubernetes configuration alert](docs/assets/robusta-namespaced-alert.png)

**Cluster-scoped permission drift**

![Cluster-scoped Kubernetes RBAC alert](docs/assets/robusta-cluster-alert.png)

## Security behavior

- Webhook URLs remain in `robusta-alert-webhooks`.
- The relay has no Kubernetes service-account token.
- The deployment-time compiler receives only `get`/`patch` access to the named internal configuration Secret and named runner Deployment.
- Compiler permissions are removed after the deployment hook succeeds.
- Secret watching remains disabled unless `WATCH_SECRETS=true`.
- Secret data values are redacted and value-only Secret changes are not observable in the pinned watcher version.
- Application logs are not collected by Profile-generated health monitoring.
- Prometheus, HolmesGPT, Robusta SaaS telemetry, and the Robusta UI are disabled or unnecessary.

## Architecture

```text
Kubernetes events
  → exact Profile scope and operation matching
  → relevant old/new comparison or health detection
  → package relay
  → Mattermost and/or Slack
```

The package deterministically compiles Profiles during deployment and validates the generated monitoring configuration against the packaged Robusta `0.48.0` runtime before activation. A package-managed Robusta action verifies workload ownership for Pod health findings. Users normally interact only with Profiles and external webhook Secret keys.

Prometheus remains responsible for metric-based application behavior such as latency, error rate, saturation, and SLO alerts. Profiles focus on Kubernetes runtime failures, resource changes, context, and routing; they are not a Grafana or Prometheus replacement.

## Advanced configuration

See [`docs/configuration.md`](docs/configuration.md) for the complete user reference and [`docs/profile-product-direction-assessment.md`](docs/profile-product-direction-assessment.md) for implemented behavior, verified product boundaries, and deferred work.

The configuration reference covers:

- the complete Profile schema;
- exact drift semantics;
- supported resources and categories;
- automatic health defaults, ownership, cooldowns, and opt-outs;
- multiple destinations;
- Secret opt-in behavior;
- Profile validation details.
