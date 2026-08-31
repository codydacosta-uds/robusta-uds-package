# Configuration

The package uses the official Robusta chart version `0.48.0`.

## Defaults

- Robusta namespace: `robusta`
- Alert/test namespace: `robusta-test`
- Environment: `dev`
- Mattermost relay: enabled by default
- Prometheus stack and HolmesGPT: disabled

The package observes cluster-wide resources but emits findings only for the reviewed `robusta-test` ConfigMap playbook by default. Expand the allow-list declaratively in `values/upstream-values.yaml`.

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
