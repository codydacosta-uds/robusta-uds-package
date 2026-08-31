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

The `test` flavor deploys a disposable in-cluster Mattermost-compatible receiver and is used by CI to verify the complete alert delivery path. The receiver and test Secret are never included in the default `upstream` flavor.

Source assets were copied from `toolbox/robusta`; that source directory is not modified.
