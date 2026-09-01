# Profile architecture

This contributor document records the Profile implementation boundary. The user-facing API is documented in [configuration.md](configuration.md).

## Pinned contracts

- Robusta chart/app/runner: `0.48.0`, upstream commit `22e6dc19cbd18d1042177a36d9ff1eee5feae4de`
- Kubewatch: `v2.16.1`, upstream commit `d821119e025a2f81ec3da66a6465331a7451328d`

Profile mappings must be reverified before either dependency is upgraded.

## Components

```text
Profile configuration
  → profile_model.py validation, applicability, and default resolution
  → profile_compiler.py deterministic playbooks
  → exact Robusta 0.48.0 schema validation
  → robusta-playbooks-config-secret
  → Robusta file reload

Kubewatch event
  → generated Robusta scope/operation/filter
  → change diff or health finding
  → internal text or JSON webhook
  → relay semantic classification/rendering
  → external destination
```

## Compiler

The compiler is a post-install/post-upgrade Helm hook using the already packaged `robusta-runner:0.48.0` image. It:

1. Validates and normalizes lowercase resource mappings.
2. Resolves applicable baseline health defaults and explicit opt-outs.
3. Generates stable playbook names from the Profile-name SHA-256 digest.
4. Generates exact namespace/name/label scopes for changes.
5. Generates namespace-coarse Pod health triggers followed by exact ownership filtering.
6. Generates create/delete, drift-update, and health playbooks.
7. Parses the complete result with Robusta's packaged `RunnerConfig` and action parameter classes.
8. Replaces only playbooks prefixed `UdsProfileV2`.
9. Applies the result to `robusta-playbooks-config-secret`.
10. Patches a package-owned Pod-template activation annotation on `robusta-runner` so the runner starts with the generated file immediately.

Zarf applies Helm resources using server-side apply with field manager `zarf`. The compiler deliberately uses the same manager and an apply patch so subsequent package upgrades do not conflict with the generated `active_playbooks.yaml` field.

The rollout is required because Kubernetes projected Secret refresh can lag package completion. Waiting for eventual file reload allowed events immediately after deployment to be missed in fresh-cluster testing.

The hook ServiceAccount receives only `get` and `patch` for the named Secret and `get`/`patch` for the named `robusta-runner` Deployment. Hook resources are deleted after success.

## Robusta responsibilities

Generated Robusta playbooks own:

- informer event consumption;
- create/update/delete selection;
- exact Profile scope;
- coarse update filtering;
- old/new diff production;
- health-state detection;
- health rate limiting and delays;
- optional Kubernetes Event enrichment.

Unscoped upstream builtin and priority playbooks are disabled.

## Workload ownership action

The package mounts `uds_profile_actions` into the Robusta runner and registers `profile_ownership_filter` through Robusta's verified local playbook repository interface. Pod health triggers use namespace as a coarse scope, then the action resolves the direct Pod controller. ReplicaSet ownership is followed once more to identify a Deployment.

The action compares exact configured names and Profile labels against the resolved workload. On a mismatch or Kubernetes read failure it clears only that playbook's target sinks, allowing other Profiles to evaluate the same event. It does not set Robusta's global `stop_processing` flag. The relay remains outside this ownership path and retains no Kubernetes credentials.

## Relay responsibilities

The relay owns:

- final exact semantic category classification;
- Profile destination resolution;
- Secret-key URL lookup;
- Mattermost and Slack formatting;
- defensive Secret redaction;
- bounded external delivery.

It does not watch or read Kubernetes resources and retains `automountServiceAccountToken: false`.

Final semantic classification is required because Robusta 0.48.0 implements `change_filters.include` as case-sensitive substring matching over Hikaru's formatted path. For example, coarse `image` filtering also admits `imagePullPolicy`; the relay rejects the latter for an image-only Profile.

## Internal transports

Two internal webhook sinks are used:

- `profile-change-relay`: text, because Robusta 0.48.0 fails to JSON-serialize nested Hikaru objects in `KubernetesDiffBlock`.
- `profile-health-relay`: JSON, because ordinary health findings serialize reliably and include subject metadata.

The generated title marker is an internal versioned envelope:

```text
UDS_PROFILE_V2|<profile-id>|<signal>|<operation>|<kind>|<namespace>|<name>
```

It is not a public event schema.

## Drift boundary

Drift means a qualifying update from the previous object delivered by Kubewatch. The package does not persist a desired baseline or ingest Git/Terraform/Helm state. It must not claim actor attribution or desired-state reconciliation.

## Health safety

Generated health playbooks never use:

- `report_crash_loop`;
- `logs_enricher`;
- OOM log attachment;
- Prometheus memory graphs;
- node `dmesg`.

Supported health triggers are limited to the exact packaged implementations for crash loops, image-pull backoff, OOM kills, failed Jobs, and Pod eviction.

## Tests required for changes

Any Profile mapping change must retain:

- schema positive and negative tests;
- deterministic compiler tests;
- exact Robusta runtime parsing;
- exact namespace/name/label scope tests;
- create/update/delete runtime tests;
- semantic positive and negative tests;
- all five health-signal runtime tests;
- Mattermost and Slack rendering tests;
- Secret redaction tests;
- immediate post-deployment event tests proving Profile activation;
- repeated-upgrade tests proving no server-side apply conflict;
- clean-install tests.
