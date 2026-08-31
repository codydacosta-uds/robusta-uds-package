# UDS Robusta Package

Packages the official Robusta Helm chart `0.48.0` with profile-driven native Kubernetes resource alerts and a uniform Mattermost attachment relay.

Users configure:

- One or more exact namespaces per namespaced alert profile
- One or more supported resource types per profile
- Default or profile-specific named sinks
- Separate cluster-scoped profiles

Users do not need to write Robusta playbooks or webhook payloads.

## Defaults

- Install namespace: `robusta`
- `zarf-namespaced-resources`: enabled, exact namespace `zarf`, yellow attachments
- `cluster-scoped-resources`: enabled, red attachments
- Default sink: `mattermost-default`, mapped to Secret key `url`
- Secret observation: disabled until explicitly enabled
- Prometheus stack and HolmesGPT: disabled

Create the externally managed webhook Secret before deployment:

```bash
kubectl create namespace robusta --dry-run=client -o yaml | kubectl apply -f -
kubectl -n robusta create secret generic robusta-mattermost-webhook \
  --from-literal=url='https://mattermost.example/hooks/REPLACE_ME'
```

See [Alert profile configuration](docs/configuration.md) for the complete schema, supported resources, multiple namespace examples, named sink overrides, Secret opt-in, and deployment commands.

The `test` flavor uses a disposable in-cluster Mattermost-compatible receiver to verify the complete ConfigMap alert path. No real credentials are included in either flavor.
