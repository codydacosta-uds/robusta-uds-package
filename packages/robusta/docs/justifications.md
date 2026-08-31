# Validation and security justifications

- Mattermost webhook URL is intentionally external and must be supplied through the `robusta-mattermost-webhook` Kubernetes Secret; no credential is packaged.
- Robusta has no user-facing HTTP UI or OIDC configuration in chart `0.48.0`, so the Package CR does not define Istio exposure, SSO, or identity-authorization integration.
- The bundled Prometheus stack and HolmesGPT dependencies are disabled by default; no UDS database, object-store, or Valkey dependency is required by this configuration.
- The relay uses the upstream `python:3.13.2-alpine` image and has no Kubernetes API token (`automountServiceAccountToken: false`).
