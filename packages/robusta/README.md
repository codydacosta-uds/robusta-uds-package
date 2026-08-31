# UDS Robusta Package

Packages the official Robusta Helm chart `0.48.0` from `https://robusta-charts.storage.googleapis.com`.

Defaults:

- Namespace: `robusta`
- Environment: `dev`
- Mattermost relay enabled
- Default alert scope: the `zarf` namespace for ConfigMaps and Deployments
- No real credentials

The Mattermost webhook is intentionally not packaged. Before deployment, create it externally:

```bash
kubectl -n robusta create secret generic robusta-mattermost-webhook \
  --from-literal=url='https://mattermost.example/hooks/REPLACE_ME'
```

The relay reads key `url` from that Secret. The package does not create or populate the Secret.

## Files to update before deployment

1. **`values/upstream-values.yaml`** — set `clusterName`, `globalConfig.account_id`, and `globalConfig.signing_key`; update the `customPlaybooks` namespace filters and environment-specific alert rules. Keep this file private if it contains real credentials.
2. **`manifests/mattermost-relay.yaml`** — update `ALERT_ENVIRONMENT` under the relay Deployment from `dev` to the target environment. Keep the Secret reference unchanged.
3. **Kubernetes Secret (not a repository file)** — create `robusta-mattermost-webhook` in namespace `robusta`, with key `url` containing the Mattermost webhook URL.
4. **`bundle/uds-bundle.yaml`** — normally no changes are needed. Add dependency packages here only if the selected Robusta configuration enables them.

The checked-in `dev` values are safe placeholders only and are not production credentials. Do not commit real webhook URLs, signing keys, or account credentials.

## Deployment inputs

Before deployment, provide:

- A UDS Core cluster with the base layer and outbound HTTPS access to the Mattermost host.
- Approval of the default `zarf` namespace alert scope or a reviewed replacement in `customPlaybooks`.

Build and deploy from this directory:

```bash
uds zarf package create --flavor upstream --skip-sbom --confirm
uds create bundle/ --confirm
uds deploy bundle/uds-bundle-robusta-test-*-dev.tar.zst --confirm
```

The webhook Secret must exist before deployment because the relay workload references it. Replace `values/upstream-values.yaml` through your deployment workflow; do not commit real webhook URLs, signing keys, or account credentials.

Source assets were copied from `toolbox/robusta`; that source directory is not modified.
