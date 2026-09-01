# Profile product direction and implementation status

This document records how the product direction maps to the implemented Profile behavior and identifies work that remains intentionally deferred.

## Product goal

Creating a Profile enrolls an application into a sane, low-noise Kubernetes operational safety net. Users describe application resources with Kubernetes concepts and do not configure Robusta playbooks, triggers, actions, Kubewatch, Prometheus internals, or raw diff paths.

The package optimizes for meaningful operational notifications, not maximum event volume. It is not a Grafana replacement, Prometheus rule editor, SLO engine, or general Kubernetes event firehose.

## Implemented in this release

### Application-focused resource configuration

Resources use lowercase mapping keys with optional exact names:

```yaml
profiles:
  - name: gitlab-production
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
      configmap: {}
```

Resource keys are case-insensitive during validation. Documentation uses lowercase consistently. `{}` or an omitted value selects all matching resources of that type. Names apply only to their resource type.

### Automatic baseline health

`monitor.health` is optional. Applicable signals are enabled automatically:

| Resource | Automatic signals |
|---|---|
| `deployment`, `statefulset`, `daemonset`, `replicaset`, `pod` | Crash loop, image-pull failure, OOM kill, Pod eviction |
| `job` | All Pod signals plus Job failure |
| Configuration, networking, autoscaling, storage, scheduling, and RBAC resources | None |

Signals can be disabled individually:

```yaml
monitor:
  health:
    oomKill: false
```

The complete baseline can be disabled and selected signals re-enabled:

```yaml
monitor:
  health:
    defaults: false
    jobFailure: true
```

An explicitly enabled inapplicable signal fails validation.

### Verified Pod ownership

A package-managed Robusta action verifies Pod ownership before a Profile health finding is created:

```text
Pod → ReplicaSet → Deployment
Pod → StatefulSet
Pod → DaemonSet
Pod → ReplicaSet
Pod → Job
```

Standalone Pods match the Profile's `pod` resource directly. Workload names and labels are evaluated against the resolved owning workload. The action fails closed when ownership cannot be verified. It does not assume generated Pod names and does not treat every Pod in a namespace as part of every Profile.

The action runs inside the packaged Robusta runner using its existing read access. The alert relay remains isolated with no Kubernetes service-account token.

### Health and changes remain distinct

Health answers **what is wrong** and is enabled automatically for applicable workloads.

Changes answer **what changed** and remain explicit because deployments, replica adjustments, ConfigMap updates, and Pod replacement are often routine. A minimal workload Profile therefore produces health notifications without automatically generating resource-change notifications.

Existing lifecycle and semantic drift monitoring remains available when independent change notification is required.

### Low-noise package default

An empty `PROFILE_CONFIG` installs only `uds-core-health`. It applies automatic workload health to Deployments, StatefulSets, DaemonSets, ReplicaSets, and Jobs in the exact `zarf` namespace. Broad manifest drift, lifecycle changes, standalone Pods, Secrets, and cluster-scoped resources remain opt-in because the package cannot infer which changes are meaningful to each installation.

### Existing noise controls

The exact Robusta `0.48.0` triggers provide:

| Signal | Behavior |
|---|---|
| Crash loop | Requires `CrashLoopBackOff` and at least two restarts; four-hour rate limit |
| Image pull failure | Requires persistent `ImagePullBackOff` for at least 120 seconds; four-hour rate limit |
| OOM kill | Requires current `OOMKilled` state; one-hour rate limit |
| Pod eviction | Requires Pod reason `Evicted`; four-hour rate limit |
| Job failure | Fires only when the Job transitions into Failed |

These cooldowns are held in runner memory and reset when the runner restarts. They reduce repeated notifications but are not durable incident state.

The relay retries transient destination failures twice, after bounded one- and two-second delays. It does not maintain a persistent delivery queue.

### Safe context

- Safe Kubernetes Event context defaults on for active health monitoring.
- Application logs are not collected.
- Prometheus graphs are not attached.
- Node `dmesg` and command output are not attached.
- Secret redaction remains enforced.

## Runtime validation completed

The implementation was validated using a clean Multipass UDS Core cluster and the packaged Robusta `0.48.0` runtime.

Tests cover:

- deterministic Profile validation and compilation;
- lowercase and mixed-case resource keys;
- exact per-resource names;
- implicit defaults and complete/individual opt-outs;
- exact Robusta action and parameter registration;
- Deployment ownership through ReplicaSet;
- direct StatefulSet, DaemonSet, ReplicaSet, Job, and Pod ownership;
- all five health signals;
- unrelated workloads in the same namespace producing no Profile health notification;
- OOM opt-out producing no OOM notification;
- repeated persistent CrashLoop updates remaining rate limited;
- lifecycle and semantic drift regression behavior;
- Mattermost and Slack delivery;
- safe Event context without application logs;
- repeated package upgrades without server-side-apply conflicts; and
- package readiness with zero component restarts.

## Intentionally deferred

### Durable incident state

The package does not yet maintain durable open/resolved incidents across runner replacement. Destination deduplication and in-memory trigger cooldowns must not be described as durable incident deduplication.

### Change-to-health correlation

Changes are not yet retained quietly and attached to a later health finding. The package does not claim that a previous change caused an incident.

### Maintenance windows

Runtime maintenance state is not implemented. The intended future behavior remains:

```text
routine changes during maintenance → suppress
health failures during maintenance → continue notifying
```

Runtime activation must remain separate from Profile policy and must not require a Git change for every window.

### Generic warning Events

Robusta's warning trigger exists, but generic Event scoping does not reliably preserve involved-workload Profile labels. Warning Events are not enabled as a default until involved-resource mapping is proven safe.

### Prometheus ingestion

Prometheus remains responsible for latency, error rate, saturation, capacity, and SLO alerts. Profiles do not recreate those rules. Profile-aware ingestion and enrichment of existing Prometheus alerts is not implemented yet.

### Human-readable inspection command

Profiles are validated and compiled deterministically, but a supported inspection command that prints the complete resolved policy remains future work.

## Documentation rule

The README and configuration reference describe only behavior implemented and tested in the package. Planned maintenance, durable incidents, correlation, Prometheus ingestion, and inspection capabilities must remain clearly identified as unavailable until their runtime paths are complete.
