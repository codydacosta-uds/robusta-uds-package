# Profile configuration

Profiles are the package's user-facing monitoring API. A Profile defines the Kubernetes resources that make up an application or operational boundary. Applicable workloads are automatically enrolled in baseline health monitoring; users configure additional behavior only when they need opt-outs, change notifications, different destinations, or narrower scope. Users do not write Robusta playbooks.

> [!WARNING]
> Profiles are a Defense Unicorns package abstraction over the pinned Robusta `0.48.0` and Kubewatch `v2.16.1` implementations. `PROFILE_CONFIG` is not directly portable to or from an upstream Robusta values file.

## Quick example

```yaml
schemaVersion: 1

defaultDestinations: [alerts-default]

destinations:
  alerts-default:
    type: mattermost
    secretKey: alerts-default-url

profiles:
  - name: application-production
    scope:
      namespaces:
        - application
    resources:
      deployment:
        names:
          - application-web
          - application-worker
      statefulset:
        names:
          - application-data
      configmap: {}
```

The complete readable example is [`examples/profile-config.yaml`](../examples/profile-config.yaml).

## Built-in Profiles

When `PROFILE_CONFIG` is empty, the package installs one low-noise Profile:

| Profile | Scope | Resources | Monitoring |
|---|---|---|---|
| `uds-core-health` | Exact `zarf` namespace | Deployment, StatefulSet, DaemonSet, ReplicaSet, and Job | Applicable automatic health signals |

The built-in Profile uses the `alerts-default` Mattermost destination and `alerts-default-url` Secret key. It intentionally does not enable manifest drift, create/delete notifications, cluster-resource monitoring, standalone Pod monitoring, or Secret watching. The package cannot infer which of those changes are operationally meaningful for a particular installation.

Supplying `PROFILE_CONFIG` replaces the built-in Profile; it does not merge with it. Include every Profile and destination the deployment needs. Copy the `uds-core-health` boundary into custom configuration when UDS Core health coverage should remain.

## What drift means

A Profile drift signal is a qualifying live update from the resource state previously observed by Kubernetes:

```text
previous in-cluster object
  → Kubernetes update
  → relevant old/new field difference
  → Profile drift notification
```

It catches unplanned `kubectl` edits, controller mutations, release changes, and other live configuration divergence selected by the Profile. The notification includes the exact observed old/new fields.

It does **not** claim:

- comparison against Git, Terraform state, Helm source, or another desired-state repository;
- identification of the user or controller that made the change;
- proof that the change was unauthorized;
- detection of drift that occurred while Kubewatch was offline.

Those capabilities require an audit or desired-state source that this package does not currently ingest.

## Schema

### Top level

| Field | Required | Meaning |
|---|---:|---|
| `schemaVersion` | Yes | Must be `1` |
| `defaultDestinations` | Yes | Destination names used when a Profile omits an override |
| `destinations` | Yes | Logical destination names mapped to external Secret keys |
| `profiles` | Yes | One or more Profile definitions |

### Destination

```yaml
destinations:
  alerts-default:
    type: mattermost       # mattermost or slack
    secretKey: alerts-default-url
```

Webhook URLs never belong in Profile configuration. `secretKey` refers to a key in the externally managed `robusta-alert-webhooks` Secret.

### Profile

```yaml
profiles:
  - name: application-production
    enabled: true
    scope: {}
    resources: {}
    monitor: {}
    context: {}
    notify: {}
```

`enabled` defaults to `true`. A disabled Profile is validated but generates no active monitoring playbooks.

## Scope

```yaml
scope:
  namespaces: [application, application-dev]
  labels:
    app.kubernetes.io/part-of: application
    team: platform
```

- Namespace matching is exact.
- Resource names are configured under each resource type and matched exactly.
- All configured labels must match exact values on the selected resource or owning workload.
- Multiple namespaces and resource names are alternatives.
- A namespaced resource requires at least one namespace.
- A cluster-scoped resource cannot use `scope.namespaces`.
- Namespaced and cluster-scoped resources cannot be mixed in one Profile.
- Use separate Profiles when an application requires both kinds of coverage.

Profiles do not expose regexes, label expressions, annotation selectors, exclusions, or arbitrary Robusta attributes.

## Resources

```yaml
resources:
  deployment:
    names:
      - webservice
      - sidekiq
  statefulset:
    names:
      - gitaly
  configmap: {}
```

Resource types are lowercase mapping keys. Matching is case-insensitive, so accidental capitalization is normalized rather than rejected. `names` selects exact objects of that type. `{}` or an omitted value selects every object of that type matching the Profile namespaces and labels. Different resource types can therefore use different exact names without separate Profiles.

### Namespaced resources

- `configmap`
- `daemonset`
- `deployment`
- `horizontalpodautoscaler`
- `ingress`
- `job`
- `pod`
- `replicaset`
- `secret` with explicit opt-in
- `service`
- `serviceaccount`
- `statefulset`

### Cluster-scoped resources

- `clusterrole`
- `clusterrolebinding`
- `namespace`
- `node`
- `persistentvolume`

HorizontalPodAutoscaler observation uses `autoscaling/v1` in the pinned Kubewatch version. The package does not promise visibility into `autoscaling/v2`-only metric fields.

## Health versus changes

Health answers **what is wrong** and is enabled automatically for applicable workloads. Changes answer **what changed** and are explicit because deployments, replica adjustments, ConfigMap updates, and Pod replacement are often routine operations.

A minimal workload Profile therefore produces health notifications but no resource-change notifications. Add lifecycle or drift configuration only when those changes deserve independent notification. The package does not currently retain quiet changes for later incident correlation and never claims that a preceding change caused a health failure.

## Resource lifecycle changes

```yaml
monitor:
  changes:
    events: [create, delete]
```

Supported lifecycle events are `create` and `delete`. Updates are configured as drift so users must state which live fields matter.

Kubewatch does not emit create notifications for objects that already existed when its informer started.

## Drift categories

```yaml
monitor:
  drift:
    include: [image, replicas, resources]
```

| Category | Supported kinds | Meaning |
|---|---|---|
| `image` | Workloads, Pods, Jobs | Container and init-container image |
| `replicas` | Deployment, StatefulSet, ReplicaSet | Desired replica count |
| `resources` | Workloads, Pods, Jobs | Container requests and limits |
| `environment` | Workloads, Pods, Jobs | `env` and `envFrom` |
| `volumes` | Workloads, Pods, Jobs | Volumes and volume mounts |
| `configuration` | ConfigMap | `data` and `binaryData` |
| `networking` | Service, Ingress | Ports, selectors, routes, TLS, and backends |
| `autoscaling` | HorizontalPodAutoscaler | v1 scaling specification |
| `permissions` | ClusterRole, ClusterRoleBinding, ServiceAccount | RBAC and service-account access fields |
| `scheduling` | Node | Taints and schedulability |
| `storage` | PersistentVolume | Capacity, class, access, reclaim, and volume source |
| `labels` | All supported kinds | Kubernetes labels |
| `annotations` | All supported kinds | Kubernetes annotations |
| `manifest` | All supported kinds | Broad kind-specific configuration |

A selected category must apply to at least one resource, and every drift-monitored resource must have an applicable selected category.

Robusta `0.48.0` performs only coarse substring filtering. The package relay performs a final exact category check against Robusta's generated diff paths. For example, `image` accepts `containers.0.image` and rejects `containers.0.imagePullPolicy`.

Automatic noise filtering removes:

- `status`
- `metadata.generation`
- `metadata.resourceVersion`
- `metadata.managedFields`

Raw Robusta filter paths are not part of the public Profile contract.

## Automatic health

`monitor.health` may be omitted. The compiler enables only signals applicable to the selected resources:

| Selected resource | Automatic signals |
|---|---|
| `deployment`, `statefulset`, `daemonset`, `replicaset`, `pod` | `crashLoop`, `imagePullFailure`, `oomKill`, `podEviction` |
| `job` | All Pod signals plus `jobFailure` |
| ConfigMap, networking, autoscaling, storage, scheduling, and RBAC resources | None |

A Profile containing only resources without applicable health must configure lifecycle changes or drift; otherwise it would monitor nothing and validation fails.

### Opting out

Override only signals that are not useful for the application:

```yaml
monitor:
  health:
    oomKill: false
    podEviction: false
```

Disable the complete baseline with `defaults: false`. Signals can then be enabled individually:

```yaml
monitor:
  health:
    defaults: false
    jobFailure: true
```

A signal explicitly enabled for an inapplicable resource fails validation rather than generating an ineffective policy.

### Pod ownership

Pod-level failures are associated with Profile resources through verified controller ownership:

```text
Pod → ReplicaSet → Deployment
Pod → StatefulSet
Pod → DaemonSet
Pod → ReplicaSet
Pod → Job
```

Standalone Pods match the Profile's `pod` resource settings directly. The package-managed Robusta ownership action checks exact workload names and labels before producing a finding. It fails closed if ownership cannot be read, and does not treat every Pod in a namespace as part of every Profile.

### Signal behavior and noise controls

| Signal | Detection and repeated-notification behavior |
|---|---|
| `crashLoop` | Requires `CrashLoopBackOff` and at least two restarts; rate limited for four hours per Robusta playbook and direct Pod owner |
| `imagePullFailure` | Requires persistent `ImagePullBackOff` for at least 120 seconds; rate limited for four hours |
| `oomKill` | Requires a current `OOMKilled` container state; rate limited for one hour |
| `podEviction` | Requires Pod reason `Evicted`; rate limited for four hours per Pod |
| `jobFailure` | Fires only when a matching Job transitions from not failed to Failed |

Robusta `0.48.0` stores these rate limits in runner memory. They reduce repeated notifications but reset when the runner restarts; they are not durable incident state or resolution tracking.

Unscoped upstream health playbooks are disabled. Generated Profile health playbooks do not attach application logs, Prometheus graphs, or node `dmesg` output.

## Context

```yaml
context:
  diff: true
  kubernetesEvents: true
```

- `diff` defaults to `true` for drift findings.
- `kubernetesEvents` defaults to `true` when the Profile has active health signals and adds safe Event context to supported findings. Set it to `false` to omit Event context.
- Application logs are not available as Profile context because they can contain credentials or sensitive data.

## Notifications

```yaml
notify:
  severity: high
  destinations: [alerts-default, security-alerts]
```

Valid severity values are:

- `debug`
- `info`
- `low`
- `high`

A Profile without `notify.destinations` uses `defaultDestinations`. Duplicate destination names are normalized so one finding is sent once to each selected destination.

Destination normalization is not durable incident deduplication. Health-trigger cooldowns are documented above; persistent incident state, resolution notifications, maintenance windows, and cross-signal correlation are not currently implemented.

The relay retries a destination request after one second and then after two seconds, for at most three attempts. This protects against brief connection and TLS failures without creating an unbounded retry storm. Retry state is not persisted; a notification that fails all three attempts is logged and returned to Robusta as a delivery failure.

## Prometheus relationship

Profiles do not replace Prometheus or Grafana. Prometheus remains appropriate for metric-based application behavior such as latency, error rates, saturation, capacity, and SLO conditions. This package focuses on Kubernetes runtime failures, resource changes, Kubernetes context, and notification routing. Existing Prometheus alert rules do not need to be recreated in Profiles; Profile-aware ingestion and enrichment of external Prometheus alerts is not currently implemented.

## Webhook Secret

Create the Secret before package deployment:

```bash
kubectl create namespace robusta --dry-run=client -o yaml | kubectl apply -f -
kubectl -n robusta create secret generic robusta-alert-webhooks \
  --from-literal=alerts-default-url='https://mattermost.example/hooks/REPLACE_ME'
```

For multiple destinations, add one key per destination. Never commit webhook URLs, Robusta signing keys, or account credentials.

## Package deployment

Convert readable Profile YAML to compact JSON:

```bash
PROFILE_CONFIG="$(yq -o=json -I=0 examples/profile-config.yaml)"

uds zarf package deploy zarf-package-robusta-amd64-<version>-upstream.tar.zst \
  --set-variables CLUSTER_NAME=my-cluster \
  --set-variables ALERT_ENVIRONMENT=development \
  --set-variables "PROFILE_CONFIG=${PROFILE_CONFIG}" \
  --confirm
```

Or use the multiline JSON example in [`examples/uds-config.yaml`](../examples/uds-config.yaml).

## Secret observation

Secret observation remains disabled by default. A Profile containing `Secret` also requires:

```bash
--set-variables WATCH_SECRETS=true
```

Kubewatch intentionally redacts Secret values before comparing old and new objects. The package can observe Secret labels, annotations, type, and some key-structure changes, but cannot detect Secret data-value changes. The relay retains defensive redaction.

## Package variables

- `CLUSTER_NAME`: Robusta cluster name; default `robusta-cluster`.
- `ROBUSTA_ACCOUNT_ID`: Optional account ID.
- `ROBUSTA_SIGNING_KEY`: Optional signing key supplied securely.
- `ALERT_ENVIRONMENT`: Notification environment label; default `production`.
- `PROFILE_CONFIG`: Profile JSON. Leave it empty to use the package's built-in `uds-core-health` Profile.
- `WATCH_SECRETS`: Explicit Secret-observation gate; default `false`.
