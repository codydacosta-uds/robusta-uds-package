# UDS Robusta Package

This package deploys Robusta on UDS Core with profile-driven Kubernetes resource alerts and uniform Mattermost notifications.

Users select exact namespaces, supported resource types, and named Mattermost sinks. The package owns the Robusta playbooks and webhook payload formatting; users do not need to write either.

## Prerequisites

- A Kubernetes cluster with UDS Core installed.
- A Mattermost-compatible incoming webhook reachable from the cluster over HTTPS (`443`). Its hostname must resolve from the cluster and its certificate must be trusted by the relay container.
- An externally managed Secret named `robusta-mattermost-webhook` in the `robusta` namespace. Each configured sink maps to a key in this Secret.

A Robusta SaaS account, external database, object storage, storage class, UI/SSO configuration, and Prometheus stack are not required for the Mattermost resource-alert workflow. `ROBUSTA_ACCOUNT_ID` and `ROBUSTA_SIGNING_KEY` remain optional.

## Getting started

### 1. Create the webhook Secret

The package never stores webhook URLs in package variables or configuration files. Create the externally managed Secret before deployment:

```bash
kubectl create namespace robusta --dry-run=client -o yaml | kubectl apply -f -
kubectl -n robusta create secret generic robusta-mattermost-webhook \
  --from-literal=url='https://mattermost.example/hooks/REPLACE_ME'
```

For multiple destinations, create one key per webhook. Profiles refer to friendly sink names that map to these keys; see [`examples/alert-config-multiple-sinks.yaml`](examples/alert-config-multiple-sinks.yaml).

### 2. Choose what to watch

Start with the provided profile:

```bash
cp examples/alert-config.yaml my-alert-config.yaml
```

Edit `my-alert-config.yaml` to select exact namespaces and resources. Namespace matching is exact: `application` does not match `application-dev`.

### 3. Deploy using one of these methods

#### Option A: deploy the Zarf package directly

Use this method when you have a released `zarf-package-robusta-...tar.zst` artifact:

```bash
PACKAGE=zarf-package-robusta-amd64-<version>-upstream.tar.zst
ALERT_CONFIG="$(yq -o=json -I=0 my-alert-config.yaml)"

uds zarf package deploy "$PACKAGE" \
  --set-variables CLUSTER_NAME=my-cluster \
  --set-variables ALERT_ENVIRONMENT=development \
  --set-variables WATCH_SECRETS=false \
  --set-variables "ALERT_CONFIG=${ALERT_CONFIG}" \
  --confirm
```

`ALERT_CONFIG` is JSON at the package boundary. Keeping the source as YAML and converting it with `yq` makes profile editing easier.

#### Option B: add Robusta to `uds-config.yaml`

Merge the package variables under `variables.robusta` in your environment's existing `uds-config.yaml`:

```yaml
variables:
  robusta:
    CLUSTER_NAME: "my-cluster"
    ALERT_ENVIRONMENT: "development"
    WATCH_SECRETS: "false"
    ALERT_CONFIG: |-
      {
        "defaultSinks": ["mattermost-default"],
        "sinks": {"mattermost-default": {"secretKey": "url"}},
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
- `robusta-mattermost-relay`

Test a configured namespace by changing a disposable ConfigMap:

```bash
kubectl -n application create configmap robusta-getting-started \
  --from-literal=status=before
kubectl -n application patch configmap robusta-getting-started \
  --type=merge -p '{"data":{"status":"after"}}'
kubectl -n application delete configmap robusta-getting-started
```

Replace `application` with an exact namespace included in your profile.

## Defaults

Without an `ALERT_CONFIG` override, the package uses:

- Install namespace: `robusta`
- `zarf-namespaced-resources`: enabled for exact namespace `zarf`, yellow attachments
- `cluster-scoped-resources`: enabled, red attachments
- Default sink: `mattermost-default`, mapped to Secret key `url`
- Secret observation: disabled until explicitly enabled
- Prometheus stack and HolmesGPT: disabled

## More configuration

See [Alert profile configuration](docs/configuration.md) for:

- Complete profile schema
- Supported resources
- Multiple namespace profiles
- Named sink overrides
- Secret metadata alert opt-in
- Package-variable reference
- Troubleshooting and validation behavior

The isolated test-support package under [`tests/`](tests/) provides a disposable in-cluster Mattermost-compatible receiver for CI. The root `zarf.yaml` contains only the releasable Robusta package.
