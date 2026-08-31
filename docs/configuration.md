# Configuration

The package uses the official Robusta chart version `0.48.0`.

## Defaults

- Robusta namespace: `robusta`
- Alert namespace: `zarf` (the default playbook namespace)
- Alert environment: `production`
- Mattermost relay: enabled by default
- Prometheus stack and HolmesGPT: disabled

The package observes cluster-wide resources but emits findings only for the reviewed `zarf` ConfigMap and Deployment playbooks by default. Expand the allow-list declaratively in `values/upstream-values.yaml`.

## Package variables

Set these variables when deploying the package with `uds zarf package deploy --set NAME=value`:

- `CLUSTER_NAME`: Robusta cluster name; defaults to `robusta-cluster`.
- `ROBUSTA_ACCOUNT_ID`: Optional Robusta account ID; defaults to empty.
- `ROBUSTA_SIGNING_KEY`: Optional Robusta signing key; defaults to empty. Supply this securely at deployment time.
- `ALERT_ENVIRONMENT`: Environment label added by the Mattermost relay; defaults to `production`.

## Test flavor

The `test` flavor includes a disposable in-cluster Mattermost-compatible HTTP receiver and a test-only `robusta-mattermost-webhook` Secret. CI uses it to generate a ConfigMap change and verify that the relay posts a Mattermost-formatted payload. The mock receiver is not included in the default `upstream` flavor.

## Mattermost webhook Secret

The webhook URL is external configuration and is never stored in this package. Create the Secret before deploying:

```bash
kubectl -n robusta create secret generic robusta-mattermost-webhook \
  --from-literal=url='https://mattermost.example/hooks/REPLACE_ME'
```

The relay reads `url` from this Secret. No real credentials are included in the package.

## Upstream documentation

- [Robusta documentation](https://docs.robusta.dev/)
- [Robusta Helm chart](https://github.com/robusta-dev/robusta/tree/master/helm/robusta)
- [Mattermost incoming webhooks](https://developers.mattermost.com/integrate/webhooks/incoming/)
