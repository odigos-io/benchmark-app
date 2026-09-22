# Setup

Everything here works on any Kubernetes cluster: AKS, EKS, GKE, OpenShift or your own. The
harness only needs nodes with the right labels, taints and one kubelet setting. `infra/aws/`
is a complete worked example on EKS if you want to see one end to end.

Plan on about an hour for setup. Commands run from a workstation with `kubectl` access to the
cluster, and use your current kubectl context unless you set `KUBE_CONTEXT`.

## 1. What you need

- A Kubernetes cluster, 1.28 or later, with Linux x86_64 nodes using cgroup v2 (the default on
  current node images from every major provider).
- `kubectl`, `helm` 3.12+, `docker` with `buildx`, `python3` 3.9+, `jq` and `make`.
- A container registry the cluster can pull from (ACR, ECR, Artifact Registry, GHCR, Harbor...).
- An Odigos enterprise token.

## 2. Nodes

Each cell - one configuration of the application - runs on its own node, with its database,
cache and downstream service on a second node, so nothing else competes for the application's
CPU. The load generator and the shared services each get their own nodes too.

| role | label | taint | count | size (reference) |
|---|---|---|---|---|
| application | `bench-cell=<cell>` | `bench-cell=<cell>:NoSchedule` | 1 per cell | 8 vCPU, 32 GB |
| dependencies | `bench-deps=<cell>` | `bench-deps=<cell>:NoSchedule` | 1 per cell | 4 vCPU, 16 GB |
| load generator | `bench-lane=load` | `bench-lane=load:NoSchedule` | 1 | 8 vCPU, 32 GB |
| shared | `bench-lane=shared` | none | 1-2 | 8 vCPU, 32 GB |

`<cell>` is one of `s`, `schatty`, `m`, `l`, `xl`. To test a single cell you need four nodes;
for all five, thirteen (twelve with one shared node).

The shared nodes are deliberately untainted: Odigos' control plane, the trace sink, the
orchestrator and your cluster's own add-ons land there.

Requirements that apply to every node:

- **All nodes in one availability zone.** Cross-zone latency varies and would show up as noise.
- **Non-burstable VM types.** Burstable types (Azure B-series, AWS t-family, GCP shared-core)
  measure their own CPU credits, not the workload. Any general-purpose family is fine.
- **The same VM type for all application nodes**, so cells are comparable.

Set labels and taints on the node pool rather than on individual nodes, so a replaced node
comes back correctly. If you must do it by hand:

```sh
kubectl label node <node> bench-cell=m
kubectl taint node <node> bench-cell=m:NoSchedule
```

### The kubelet setting on application nodes

Application nodes must run the kubelet **static CPU manager policy**. With it, the application
pod - which requests exactly 2 CPUs with requests equal to limits - gets two CPUs to itself,
and nothing else on the node can run on them.

```yaml
cpuManagerPolicy: static
cpuManagerPolicyOptions:
  full-pcpus-only: "true"      # recommended: the two CPUs are one physical core
```

Every managed Kubernetes service lets you set kubelet configuration per node pool, as a custom
node or kubelet configuration on the pool or in its launch template. It has to be set when
the pool is created, since the kubelet only applies it to fresh nodes.

`cpuManagerPolicy: static` is required. `full-pcpus-only` is recommended but not every provider
exposes it; without it the pod still gets exclusive CPUs, which may be hyperthreads of two
different cores, and the results remain valid. The static policy also needs some CPU reserved
for the system (kube-reserved or system-reserved); managed services reserve this by default.

Only application nodes need this. Dependency, load and shared nodes can use the defaults.

## 3. Verify the nodes

```sh
kubectl apply -f k8s/runner/namespace.yaml
make verify-nodes
```

This reads each application node's live kubelet configuration and its CPU manager state, and
exits non-zero if the static policy is not in effect. It warns, without failing, if
`full-pcpus-only` is off, and prints the CPU layout for the record. Don't go further until it
passes: on a node without the static policy the application pod runs under a CPU quota, gets
throttled, and every measurement is refused.

## 4. Build the images

Three images - the application, the echo service and the orchestrator - are built from this
repository and pushed to your registry, so nothing depends on anyone else's:

```sh
docker login <your registry>
REGISTRY=<your registry>/benchmark-app make images render
```

`render` writes `k8s/rendered/`, the manifests pointing at your images. The orchestrator image
includes kubectl 1.33; if your cluster is older than 1.32, add
`KUBECTL_VERSION=v1.<your minor>.0` to the command so kubectl stays within one version of it. If the cluster needs
credentials to pull from the registry:

```sh
REGISTRY_SERVER=<registry host> REGISTRY_USER=<user> REGISTRY_PASSWORD=<password> make registry-secret
```

## 5. Install Odigos

```sh
ODIGOS_TOKEN=<your token> make install-odigos
```

This installs the latest Odigos chart. Set `CHART_VERSION` to pin a version. Write down the
version you ran alongside your results; the reference results were produced with Odigos 1.36.

Then the shared pieces: a trace sink that counts spans and discards them, the Odigos
destination pointing at it, and a DaemonSet that reads CPU counters on the application nodes.

```sh
make deploy-shared
```

## 6. Deploy the cells

```sh
make deploy-cells                          # all five; or CELLS="m" make deploy-cells
kubectl -n cell-m rollout status deploy/bucket-app --timeout=10m
```

Each cell is a namespace (`cell-<name>`) with the application, Postgres, Redis and the echo
service. Then start the orchestrator, which runs the tests from inside the cluster so a
workstation that sleeps or disconnects doesn't interrupt them:

```sh
kubectl apply -f k8s/rendered/orchestrator.yaml
bin/incluster.sh sync
```

Setup is done. Continue with [VALIDATION.md](VALIDATION.md).

## Taking it down

Delete the `cell-*`, `bench-runner` and `bench-sink` namespaces, and uninstall Odigos with
`helm -n odigos-system uninstall odigos`. On EKS with the example cluster,
`infra/aws/teardown.sh` removes everything.

## Example: EKS

`infra/aws/cluster.yaml` is the reference cluster as an `eksctl` config: thirteen nodes
(`m6i.2xlarge` for application, load and shared nodes, `m6i.xlarge` for dependencies), all in
one zone, with the kubelet setting applied to the application pools.
`infra/aws/create-cluster.sh` creates it and `infra/aws/teardown.sh` removes it. It's a useful
reference for the labels, taints and kubelet settings on any provider.
