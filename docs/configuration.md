# Alert profile configuration

Robusta `0.48.0` is packaged with a profile-aware Mattermost relay. Users select exact namespaces, native Kubernetes resource types, and named sinks. The package generates and owns the Robusta playbooks; users do not need to write them.

## Immediate defaults

With a valid `robusta-mattermost-webhook` Secret, the package starts alerting immediately after deployment:

- `zarf-namespaced-resources` watches supported namespaced resources in the exact `zarf` namespace and sends yellow Mattermost attachments.
- `cluster-scoped-resources` watches supported cluster-scoped resources and sends red Mattermost attachments.
- Both use the named `mattermost-default` sink.
- Secret observation is supported but remains disabled until explicitly enabled.

The readable default is [`examples/alert-config.yaml`](../examples/alert-config.yaml).

## Profile schema

```yaml
defaultSinks:                    # Required named-sink fallback
  - mattermost-default

sinks:                          # Required map of sink name to Secret key
  mattermost-default:
    secretKey: url

alertProfiles:                  # Namespaced profiles
  - name: zarf-namespaced-resources
    enabled: true               # Optional; defaults to true
    namespaces: [zarf]          # Required exact namespace names
    resources: [ConfigMap, Deployment]
    # sinks omitted: use defaultSinks

clusterAlertProfiles:           # Cluster-scoped profiles
  - name: cluster-scoped-resources
    enabled: true
    resources: [ClusterRole, Node]
    sinks: [mattermost-default] # Optional profile override
```

Validation rejects unknown fields, duplicate or invalid profile names, empty collections, unsupported resource types, invalid exact namespaces, and undefined sink references. Invalid configuration prevents the relay from becoming Ready rather than silently dropping intended alerts.

### Sink behavior

- A profile without `sinks` uses `defaultSinks`.
- A profile may select one or more named sinks.
- Multiple profiles may share a sink.
- If multiple matching profiles select different sinks, the relay sends once to each unique sink.
- Sink names never contain webhook URLs. They reference keys in the external Secret.
- Events that do not match an enabled profile are acknowledged and dropped without external delivery.

## Configure the webhook Secret

The package never stores webhook URLs. Create the namespace and Secret before deployment:

```bash
kubectl create namespace robusta --dry-run=client -o yaml | kubectl apply -f -
kubectl -n robusta create secret generic robusta-mattermost-webhook \
  --from-literal=url='https://mattermost.example/hooks/REPLACE_ME'
```

For multiple named sinks, add one key per destination:

```bash
kubectl -n robusta create secret generic robusta-mattermost-webhook \
  --from-literal=url='https://mattermost.example/hooks/DEFAULT' \
  --from-literal=application-url='https://mattermost.example/hooks/APPLICATION' \
  --from-literal=security-url='https://mattermost.example/hooks/SECURITY'
```

Then map friendly sink names to those keys, as shown in [`examples/alert-config-multiple-sinks.yaml`](../examples/alert-config-multiple-sinks.yaml).

## Provide package variables

There are two supported deployment paths. In both cases, `ALERT_CONFIG` is a JSON string at the package boundary. Webhook URLs remain in the external Kubernetes Secret and must not be placed in `ALERT_CONFIG`.

### Direct Zarf package deployment

Keep profiles readable as YAML and convert them to compact JSON with `yq`:

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
        "defaultSinks": ["mattermost-default"],
        "sinks": {"mattermost-default": {"secretKey": "url"}},
        "alertProfiles": [
          {
            "name": "application-resources",
            "namespaces": ["application"],
            "resources": ["ConfigMap", "Deployment", "Service"]
          }
        ],
        "clusterAlertProfiles": []
      }
```

A complete copyable block is available at [`examples/uds-config.yaml`](../examples/uds-config.yaml). Do not commit webhook URLs or `ROBUSTA_SIGNING_KEY` values to this file.

## Add namespace profiles

To add another namespace with the default sink, add another profile and omit `sinks`:

```yaml
alertProfiles:
  - name: application-resources
    namespaces:
      - application
    resources:
      - ConfigMap
      - Deployment
      - Ingress
      - Service
```

One profile can apply the same resource policy to multiple exact namespaces:

```yaml
  - name: application-team-resources
    namespaces: [application-one, application-two]
    resources: [Deployment, Service, StatefulSet]
```

Namespace matching is exact. A profile for `application` does not match `application-dev`.

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

Every resource uses the same bounded Mattermost attachment layout with environment, namespace or `cluster-scoped`, resource, kind, scope, severity, matching profile, a resource-aware change section, and Robusta footer.

## Secret alerts

Secret observation is disabled by default. To use `Secret` in a profile, also deploy with:

```bash
--set-variables WATCH_SECRETS=true
```

Robusta/Kubewatch 0.48.0 emits Secret metadata and type changes, but intentionally does not emit Secret data-value changes. The package therefore alerts on labels, annotations, and type only. The relay retains defensive redaction so Secret data values are never rendered if an upstream payload contains them.

## Package variables

- `CLUSTER_NAME`: Robusta cluster name; default `robusta-cluster`.
- `ROBUSTA_ACCOUNT_ID`: Optional Robusta account ID.
- `ROBUSTA_SIGNING_KEY`: Optional signing key; supply securely at deployment time.
- `ALERT_ENVIRONMENT`: Attachment environment label; default `production`.
- `ALERT_CONFIG`: Compact JSON profile configuration; defaults to the two enabled profiles above.
- `WATCH_SECRETS`: Enables Secret observation; default `false`.

## Advanced custom playbooks

The generated native-resource playbooks are package internals. Advanced maintainers may add separate Robusta playbooks, but custom playbooks are not required for profile-based native resource alerting.

## Test flavor

The `test` flavor includes an in-cluster Mattermost-compatible receiver and a test-only webhook Secret. CI deploys the production playbook set, changes a ConfigMap in the default `zarf` profile, and verifies a Mattermost attachment titled `ConfigMap changed`.
