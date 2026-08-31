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

Source assets were copied from `toolbox/robusta`; that source directory is not modified.
