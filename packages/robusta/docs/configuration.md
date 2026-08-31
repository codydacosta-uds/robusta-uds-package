# Configuration

The package uses the official Robusta chart version `0.48.0`.

## Files to update

- `values/upstream-values.yaml`: set `clusterName`, `globalConfig.account_id`, `globalConfig.signing_key`, and the reviewed `customPlaybooks` namespace/rules.
- `manifests/mattermost-relay.yaml`: set the relay Deployment's `ALERT_ENVIRONMENT` value.
- Kubernetes Secret `robusta-mattermost-webhook` in namespace `robusta`: provide key `url`. This is intentionally not a repository file.
- `bundle/uds-bundle.yaml`: no change is normally required; add dependencies only when enabling optional chart dependencies.

## Defaults

- Robusta namespace: `robusta`
- Default alert namespace: `zarf`
- Environment: `dev`
- Mattermost relay: enabled by default
- Prometheus stack and HolmesGPT: disabled

The package observes cluster-wide resources but emits findings only for the reviewed `zarf` ConfigMap and Deployment playbooks by default. Expand the allow-list declaratively in `values/upstream-values.yaml`.

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
