# Alert rule configuration

> [!WARNING]
> **Package design note**
>
> Defense Unicorns engineers designed this package's rule-based policy layer to make Robusta easier to configure. Alert rules are package-defined—not an upstream Robusta feature—so users can select exact namespaces, native Kubernetes resource types, and named sinks without writing Robusta playbooks, triggers, or actions. The package generates and owns that Robusta implementation. Because this configuration contract is package-specific, alert-rule configuration is not directly portable between upstream Robusta and this package. Moving in either direction requires translating the watch and routing configuration rather than reusing `ALERT_CONFIG` unchanged.

## Immediate defaults

With a valid `robusta-alert-webhooks` Secret, the package starts alerting immediately after deployment:

- `zarf-namespaced-resources` watches supported namespaced resources in the exact `zarf` namespace and sends yellow attachments.
- `cluster-scoped-resources` watches supported cluster-scoped resources and sends red attachments.
- Both rules use the named `alerts-default` webhook destination, whose default type is `mattermost`.
- Secret observation is supported but remains disabled until explicitly enabled.
- Robusta SaaS usage telemetry is disabled for the standalone package workflow.

The readable default is [`examples/alert-config.yaml`](../examples/alert-config.yaml).

> [!NOTE]
> `ALERT_CONFIG` is optional. Omit it to use these defaults. An override changes alert-rule selection, sink type, and routing; the package continues to own the normalized native-resource playbooks and presentation.

## Alert rule schema

```yaml
defaultSinks:                    # Required named-sink fallback
  - alerts-default

sinks:                          # Required map of sink name to type and Secret key
  alerts-default:
    type: mattermost             # Optional; mattermost (default) or slack
    secretKey: alerts-default-url

namespacedAlertRules:          # Rules for exact namespaces
  - name: zarf-namespaced-resources
    enabled: true               # Optional; defaults to true
    namespaces: [zarf]          # Required exact namespace names
    resources: [ConfigMap, Deployment]
    # sinks omitted: use defaultSinks

clusterAlertRules:              # Rules for cluster-scoped resources
  - name: cluster-scoped-resources
    enabled: true
    resources: [ClusterRole, Node]
    sinks: [alerts-default]     # Optional rule override
```

Validation rejects unknown fields, duplicate or invalid rule names, empty collections, unsupported resource types, invalid exact namespaces, and undefined sink references. Invalid configuration prevents the relay from becoming Ready rather than silently dropping intended alerts.

### Rule and sink behavior

- A rule without `sinks` uses `defaultSinks`.
- A rule may select one or more named sinks.
- Multiple rules may share a sink.
- If multiple matching rules select different sinks, the relay sends once to each unique sink.
- Sink names never contain webhook URLs. They reference keys in the external Secret.
- `type` selects `mattermost` or `slack` rendering and defaults to `mattermost` when omitted.
- Mattermost and Slack sinks can be selected by the same rule; the relay renders each destination independently.
- Events that do not match an enabled rule are acknowledged and dropped without external delivery.

## Configure the webhook Secret

The package never stores webhook URLs. Create the namespace and Secret before deployment:

```bash
kubectl create namespace robusta --dry-run=client -o yaml | kubectl apply -f -
kubectl -n robusta create secret generic robusta-alert-webhooks \
  --from-literal=alerts-default-url='https://mattermost.example/hooks/REPLACE_ME'
```

For multiple named sinks, add one key per destination:

```bash
kubectl -n robusta create secret generic robusta-alert-webhooks \
  --from-literal=alerts-default-url='https://mattermost.example/hooks/DEFAULT' \
  --from-literal=application-alerts-url='https://hooks.slack.com/services/REPLACE_ME' \
  --from-literal=security-alerts-url='https://mattermost.example/hooks/SECURITY'
```

Then map logical sink names to those keys, as shown in [`examples/alert-config-multiple-sinks.yaml`](../examples/alert-config-multiple-sinks.yaml).

## Provide package variables

There are two supported deployment paths. In both cases, `ALERT_CONFIG` is a JSON string at the package boundary. Webhook URLs remain in the external Kubernetes Secret and must not be placed in `ALERT_CONFIG`.

### Direct Zarf package deployment

Keep alert rules readable as YAML and convert them to compact JSON with `yq`:

```bash
ALERT_CONFIG="$(yq -o=json -I=0 examples/alert-config.yaml)"
uds zarf package deploy zarf-package-robusta-amd64-<version>-upstream.tar.zst \
  --set-variables CLUSTER_NAME=my-cluster \
  --set-variables ALERT_ENVIRONMENT=development \
  --set-variables WATCH_SECRETS=false \
  --set-variables "ALERT_CONFIG=${ALERT_CONFIG}" \
  --confirm
```

### `uds-config.yaml`

Merge the Robusta package variables under `variables.robusta` in the environment's existing `uds-config.yaml`:

```yaml
variables:
  robusta:
    CLUSTER_NAME: "my-cluster"
    ALERT_ENVIRONMENT: "development"
    WATCH_SECRETS: "false"
    ALERT_CONFIG: |-
      {
        "defaultSinks": ["alerts-default"],
        "sinks": {"alerts-default": {"type": "mattermost", "secretKey": "alerts-default-url"}},
        "namespacedAlertRules": [
          {
            "name": "application-resources",
            "namespaces": ["application"],
            "resources": ["ConfigMap", "Deployment", "Service"]
          }
        ],
        "clusterAlertRules": []
      }
```

A complete copyable block is available at [`examples/uds-config.yaml`](../examples/uds-config.yaml). Do not commit webhook URLs or `ROBUSTA_SIGNING_KEY` values to this file.

## Add namespaced alert rules

To add another namespace with the default sink, add another rule and omit `sinks`:

```yaml
namespacedAlertRules:
  - name: application-resources
    namespaces:
      - application
    resources:
      - ConfigMap
      - Deployment
      - Ingress
      - Service
```

One rule can apply the same resource policy to multiple exact namespaces:

```yaml
  - name: application-team-resources
    namespaces: [application-one, application-two]
    resources: [Deployment, Service, StatefulSet]
```

Namespace matching is exact. A rule for `application` does not match `application-dev`.

## Supported resources

### Namespaced resources — yellow attachments

- `ConfigMap`
- `DaemonSet`
- `Deployment`
- `HorizontalPodAutoscaler`
- `Ingress`
- `Job`
- `Pod`
- `ReplicaSet`
- `Secret` (explicit opt-in)
- `Service`
- `ServiceAccount`
- `StatefulSet`

### Cluster-scoped resources — red attachments

- `ClusterRole`
- `ClusterRoleBinding`
- `Namespace`
- `Node`
- `PersistentVolume`

Every resource uses the same bounded attachment content—environment, namespace or `cluster-scoped`, resource, kind, scope, severity, matching alert rule, a resource-aware change section, and Robusta footer—with destination-specific markup.

## Secret alerts

Secret observation is disabled by default. To use `Secret` in an alert rule, also deploy with:

```bash
--set-variables WATCH_SECRETS=true
```

Robusta/Kubewatch 0.48.0 emits Secret metadata and type changes, but intentionally does not emit Secret data-value changes. The package therefore alerts on labels, annotations, and type only. The relay retains defensive redaction so Secret data values are never rendered if an upstream payload contains them.

## Package variables

- `CLUSTER_NAME`: Robusta cluster name; default `robusta-cluster`.
- `ROBUSTA_ACCOUNT_ID`: Optional Robusta account ID.
- `ROBUSTA_SIGNING_KEY`: Optional signing key; supply securely at deployment time.
- `ALERT_ENVIRONMENT`: Attachment environment label; default `production`.
- `ALERT_CONFIG`: Compact JSON alert-rule configuration; defaults to the two enabled rules above.
- `WATCH_SECRETS`: Enables Secret observation; default `false`.

## Advanced custom playbooks

The generated native-resource playbooks are package internals. Advanced maintainers may add separate Robusta playbooks, but custom playbooks are not required for rule-based native resource alerting.

## Test support package

The isolated package in `tests/zarf.yaml` includes an in-cluster webhook receiver and a test-only webhook Secret. CI deploys it alongside the normal upstream Robusta package, changes a ConfigMap matched by the default `zarf` rule, and verifies independently rendered Mattermost and Slack attachments titled `ConfigMap changed`.
