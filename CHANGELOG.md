# Changelog

All notable changes to this package will be documented here.

## Unreleased

- Add application Profiles with lowercase per-resource configuration.
- Enable applicable crash-loop, image-pull, OOM, eviction, and Job-failure monitoring by default.
- Resolve Pod ownership through Deployment/ReplicaSet, StatefulSet, DaemonSet, ReplicaSet, Job, and standalone Pod relationships.
- Preserve explicit lifecycle and semantic drift monitoring with typed Mattermost and Slack delivery.
- Add exact ownership negatives, health opt-out, cooldown, clean-install, and repeated-upgrade tests.
- Provide a low-noise UDS Core workload-health Profile when no custom Profile configuration is supplied.
- Retry transient Mattermost and Slack delivery failures with bounded one- and two-second delays.
