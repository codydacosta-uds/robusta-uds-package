# Validation and security justifications

- Mattermost webhook URL is intentionally external and must be supplied through the `robusta-mattermost-webhook` Kubernetes Secret; no credential is packaged.
- Robusta has no user-facing HTTP UI or OIDC configuration in chart `0.48.0`, so the Package CR does not define Istio exposure, SSO, or identity-authorization integration.
- The bundled Prometheus stack and HolmesGPT dependencies are disabled by default; no UDS database, object-store, or Valkey dependency is required by this configuration.
- The relay uses the upstream `python:3.13.2-alpine` image and has no Kubernetes API token (`automountServiceAccountToken: false`). It performs exact profile matching and sink routing from mounted configuration and webhook Secret keys.
- Secret observation is disabled by default. With explicit `WATCH_SECRETS=true`, the package creates `robusta-secret-watcher`, granting only `get`, `list`, and `watch` on Secrets to the upstream forwarder and runner service accounts. The permission is required for Kubewatch observation and Robusta typed-event construction and is removed when the option is disabled. Secret alerts cover metadata/type changes; data values are not emitted externally.
- The Mattermost destination is an arbitrary external webhook host, so the relay requires an egress rule using `remoteGenerated: Anywhere`; the rule is restricted to the relay selector and TCP port 443.
- SSO is not applicable: Robusta 0.48.0 has no user-facing UI or OIDC client configuration in this package.
- Monitoring is not enabled: the selected configuration disables Robusta's bundled Prometheus stack and does not deploy a UDS monitoring layer.
- CVE-specific issue reporting and a project-specific code of conduct are deferred to the parent UDS package repository standards; the package includes the required security reporting policy and contribution guidance for review.
