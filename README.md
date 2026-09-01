# UDS Robusta Package

This package deploys Robusta on UDS Core with profile-driven Kubernetes resource alerts and uniform webhook notifications.

Users can optionally select exact namespaces, supported resource types, and named webhook destinations. The package owns the Robusta playbooks, routing, and destination-specific payload formatting; users do not need to write them.

## Out-of-the-box behavior

After the required webhook Secret is created, deploying the package **without `ALERT_CONFIG`** immediately enables these defaults:

### Namespaced change alerts

The `zarf-namespaced-resources` profile sends yellow webhook notifications for updates in the exact `zarf` namespace to:

- `ConfigMap`
- `DaemonSet`
- `Deployment`
- `HorizontalPodAutoscaler`
- `Ingress`
- `Job`
- `Pod`
- `ReplicaSet`
- `Service`
- `ServiceAccount`
- `StatefulSet`

Updates in other namespaces are not delivered unless another profile explicitly includes those namespaces.

### Cluster-scoped change alerts

The `cluster-scoped-resources` profile sends red webhook notifications for updates to:

- `ClusterRole`
- `ClusterRoleBinding`
- `Namespace`
- `Node`
- `PersistentVolume`

### Delivery and presentation

- Both profiles send to `alerts-default`, a webhook destination mapped to the `alerts-default-url` key in `robusta/robusta-alert-webhooks`. Its default destination type is `mattermost`.
- Every supported resource uses the same package-managed attachment layout: environment, namespace or `cluster-scoped`, resource, kind, scope, severity, matching profile, resource-aware change details, and Robusta footer.
- The built-in playbooks report resource **updates/changes**. They do not advertise create/delete lifecycle notifications.
- Secret observation is disabled. Events, PersistentVolumeClaims, NetworkPolicies, and arbitrary custom resources are not part of the built-in alert path.
- Prometheus, HolmesGPT, Robusta SaaS integration, usage telemetry, and a user-facing Robusta UI are not enabled or required.

> [!NOTE]
> `ALERT_CONFIG` is optional. Override it only to change exact namespaces, supported resource selections, or named-sink routing. Alert formatting and the native-resource playbooks remain package-managed.

## How the alerting pieces fit together

```text
Kubernetes resource update
  -> Robusta forwarder (Kubewatch)
  -> Robusta runner and package playbook
  -> package relay profile matching
  -> named webhook destination
```

| Piece | Purpose |
| --- | --- |
| **Forwarder / Kubewatch** | Observes supported native Kubernetes resource updates and sends them to the Robusta runner. |
| **Robusta runner** | Executes the package's internal playbook for the resource type and produces a normalized change finding. |
| **Package playbooks** | Convert supported resource events into a consistent internal finding. These are maintained by the package, not authored in `ALERT_CONFIG`. |
| **Alert profiles** | Optionally select exact namespaces, supported resources, and destination sinks. Profiles are the package's user-facing policy layer. |
| **Named sinks** | Logical destination names such as `alerts-default`. Each sink selects a supported destination type and maps to a key in the external webhook Secret; URLs are never stored in profiles. |
| **Alert relay** | Applies profile matching, deduplication, Secret redaction, per-destination formatting, routing, and the uniform attachment content. |

Robusta's upstream webhook sink is configured internally to send normalized findings to the alert relay. The named sinks exposed in `ALERT_CONFIG` represent external webhook destinations selected by users.

### Supported destination types

| Type | Status | Formatting |
| --- | --- | --- |
| `mattermost` | Supported; default | Mattermost attachment Markdown and highlighted diff blocks |
| `slack` | Supported | Slack `mrkdwn` and Slack-compatible code blocks |

The destination model is extensible; additional providers such as Teams or Discord require a validated renderer before they are advertised as supported.

## Prerequisites

- A Kubernetes cluster with UDS Core installed.
- A supported incoming webhook destination reachable from the cluster over HTTPS (`443`). Its hostname must resolve from the cluster and its certificate must be trusted by the relay container. Current destination types are `mattermost` and `slack`.
- An externally managed Secret named `robusta-alert-webhooks` in the `robusta` namespace. Each configured sink maps to a key in this Secret.

A Robusta SaaS account, external database, object storage, storage class, UI/SSO configuration, and Prometheus stack are not required for the webhook resource-alert workflow. `ROBUSTA_ACCOUNT_ID` and `ROBUSTA_SIGNING_KEY` remain optional.

## Getting started

### 1. Create the webhook Secret

The package never stores webhook URLs in package variables or configuration files. Create the externally managed Secret before deployment:

```bash
kubectl create namespace robusta --dry-run=client -o yaml | kubectl apply -f -
kubectl -n robusta create secret generic robusta-alert-webhooks \
  --from-literal=alerts-default-url='https://mattermost.example/hooks/REPLACE_ME'
```

For multiple destinations, create one key per webhook. Profiles refer to logical sink names that map to these keys; see [`examples/alert-config-multiple-sinks.yaml`](examples/alert-config-multiple-sinks.yaml).

### 2. Use the defaults or customize profiles

No profile file is required for the default behavior described above. To customize namespaces, resources, or sink routing, start with the provided profile:

```bash
cp examples/alert-config.yaml my-alert-config.yaml
```

Edit `my-alert-config.yaml` as needed. Namespace matching is exact: `application` does not match `application-dev`.

### 3. Deploy using one of these methods

#### Option A: deploy the Zarf package directly

Use this method when you have a released `zarf-package-robusta-...tar.zst` artifact:

Deploy with the default profiles:

```bash
PACKAGE=zarf-package-robusta-amd64-<version>-upstream.tar.zst

uds zarf package deploy "$PACKAGE" \
  --set-variables CLUSTER_NAME=my-cluster \
  --set-variables ALERT_ENVIRONMENT=development \
  --confirm
```

For custom profiles, convert the readable YAML file to JSON and add `ALERT_CONFIG`:

```bash
ALERT_CONFIG="$(yq -o=json -I=0 my-alert-config.yaml)"

uds zarf package deploy "$PACKAGE" \
  --set-variables CLUSTER_NAME=my-cluster \
  --set-variables ALERT_ENVIRONMENT=development \
  --set-variables "ALERT_CONFIG=${ALERT_CONFIG}" \
  --confirm
```

`ALERT_CONFIG` is JSON at the package boundary. `WATCH_SECRETS` can also be omitted unless Secret metadata alerts are intentionally enabled.

#### Option B: add Robusta to `uds-config.yaml`

Merge the package variables under `variables.robusta` in your environment's existing `uds-config.yaml`:

```yaml
variables:
  robusta:
    CLUSTER_NAME: "my-cluster"
    ALERT_ENVIRONMENT: "development"
    WATCH_SECRETS: "false"
    # Optional: omit ALERT_CONFIG to use the package defaults.
    ALERT_CONFIG: |-
      {
        "defaultSinks": ["alerts-default"],
        "sinks": {"alerts-default": {"type": "mattermost", "secretKey": "alerts-default-url"}},
        "alertProfiles": [
          {
            "name": "application-resources",
            "namespaces": ["application"],
            "resources": ["ConfigMap", "Deployment", "Ingress", "Service"]
          }
        ],
        "clusterAlertProfiles": []
      }
```

A complete example with namespaced and cluster-scoped profiles is available at [`examples/uds-config.yaml`](examples/uds-config.yaml). Do not put webhook URLs or `ROBUSTA_SIGNING_KEY` values in a committed `uds-config.yaml`; provide sensitive values through the environment's secret mechanism.

### 4. Verify the installation

```bash
kubectl -n robusta get package.uds.dev robusta
kubectl -n robusta get deployments
```

The UDS Package should report `Ready`, and these deployments should be available:

- `robusta-runner`
- `robusta-forwarder`
- `robusta-alert-relay`

Test an exact namespace included in a profile by changing a disposable ConfigMap. The default profile uses `zarf`:

```bash
NAMESPACE=zarf
kubectl -n "$NAMESPACE" create configmap robusta-getting-started \
  --from-literal=status=before
kubectl -n "$NAMESPACE" patch configmap robusta-getting-started \
  --type=merge -p '{"data":{"status":"after"}}'
kubectl -n "$NAMESPACE" delete configmap robusta-getting-started
```

## Defaults

Without an `ALERT_CONFIG` override, the package uses:

- Install namespace: `robusta`
- `zarf-namespaced-resources`: enabled for exact namespace `zarf`; watches every supported namespaced resource except opt-in Secrets and produces yellow attachments
- `cluster-scoped-resources`: enabled for every supported cluster-scoped resource and produces red attachments
- Default sink: `alerts-default` (`type: mattermost`), mapped to Secret key `alerts-default-url`
- Secret observation: disabled until explicitly enabled
- Prometheus stack, HolmesGPT, and Robusta usage telemetry: disabled

## More configuration

See [Alert profile configuration](docs/configuration.md) for:

- Complete profile schema
- Supported resources
- Multiple namespace profiles
- Named sink overrides
- Secret metadata alert opt-in
- Package-variable reference
- Troubleshooting and validation behavior

The isolated test-support package under [`tests/`](tests/) provides a disposable in-cluster webhook receiver for CI. The root `zarf.yaml` contains only the releasable Robusta package.
