# UDS Robusta Package

Packages the official Robusta Helm chart `0.48.0` from `https://robusta-charts.storage.googleapis.com`.

Defaults:

- Namespace: `robusta`
- Environment: `dev`
- Mattermost relay enabled
- Test alert scope: `robusta-test`
- No real credentials

The Mattermost webhook is intentionally not packaged. Before deployment, create it externally:

```bash
kubectl -n robusta create secret generic robusta-mattermost-webhook \
  --from-literal=url='https://mattermost.example/hooks/REPLACE_ME'
```

The relay reads key `url` from that Secret. The package does not create or populate the Secret.

## Deployment inputs

Before deployment, provide these environment-specific inputs:

1. A UDS Core cluster with the base layer and outbound HTTPS access to the Mattermost host.
2. The `robusta-mattermost-webhook` Secret in namespace `robusta`, with key `url` containing the webhook URL.
3. A unique Robusta `clusterName` and account identifier in a private values override. The checked-in `dev` values are safe placeholders only and are not production credentials.
4. Approval of the default alert scope (`robusta-test`) or a reviewed replacement in `customPlaybooks`.

Build and deploy from this directory:

```bash
uds zarf package create --flavor upstream --skip-sbom --confirm
uds create bundle/ --confirm
uds deploy bundle/uds-bundle-robusta-test-*-dev.tar.zst --confirm
```

The webhook Secret must exist before deployment because the relay workload references it. Replace `values/upstream-values.yaml` through your deployment workflow; do not commit real webhook URLs, signing keys, or account credentials.

Source assets were copied from `toolbox/robusta`; that source directory is not modified.
