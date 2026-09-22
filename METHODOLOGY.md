# Methodology

Every rule below exists because its absence has already corrupted a measurement of this
agent. The rules are enforced by the kit (`bin/lib/gate.py`, `analysis/analyze.py`), not by
the operator's discipline: an arm that violates one is rejected with the reason recorded, never
averaged in.

## 1. What is measured

**CPU per request** is the headline. It is read from the application container's own cgroup
counter (`/sys/fs/cgroup/cpu.stat`, `usage_usec`) inside the pod, together with the
container's own clock, in a single command, at the start, the middle and the end of the
measurement window. The window lies strictly inside the constant-rate measure phase (it opens
10 s after the phase starts and closes 30 s before it ends), so the number of requests served
in it is the achieved arrival rate × the window length; dividing the CPU delta by that gives
milliseconds of CPU per request with no idle tail charged to requests.

Not used: `kubectl top` / metrics-server (a sampled average with its own window), cAdvisor
divided by a wall clock read elsewhere (charged arms windows that were wrong by up to 10 s),
the runtime's own process CPU metric (sees only itself).

The two halves of the window (`cpu.h1`, `cpu.h2`) must agree within 5%. If they do not, the
process was still settling (JIT compiler threads burn CPU for minutes after a restart and that CPU
would be charged to the requests served) and the arm is rejected.

**Latency** is recorded by the load generator on a separate node, so every figure includes
the network path exactly as a caller experiences it. Reported as p50/p95/p99; an average hides
a stall affecting a small fraction of requests, which is the failure mode that matters.

**Throughput** is not a measured outcome in this kit. Load is offered at a fixed rate; an arm
that fails to deliver at least 99% of it is rejected. Section 6 explains why.

**Agent-side CPU** (odiglet, node collector) is read from their cgroups on the application
node through a read-only node probe, reported as cores and as microseconds per span. It runs
on CPUs the application does not own (see section 3), so it is not part of the application's
number; it is part of the node's.

## 2. Load model

Open model: a constant arrival rate (k6 `constant-arrival-rate`), from a Kubernetes Job on a
dedicated load node. Arrivals do not wait for responses, so a slower target is not
under-sampled, both arms handle the same request count, and coordinated omission is
structurally impossible.

Closed loop (a fixed number of virtual users with zero think time) is rejected. In that model
throughput equals users divided by latency, so any latency change is reported a second time as
a throughput change, and the "throughput drop" is not an independent observation.

Every window is warmed under the same load before it opens, ramped from zero over the first
minute, by the same k6 job that then holds the rate through the measurement - the load never
pauses between warm-up and measurement, because a pause re-warms connections and shows up as a
step in CPU per request. The first window of each repetition warms for 15 minutes after the
application starts; the later windows warm for 6 minutes each.

Warm-up is really a request count, not a time: just-in-time compilers trigger on invocation
counts (tens of thousands per method). A cell at 500 req/s gets 180,000 requests in 6 minutes;
a cell at 58 req/s gets about 21,000, which is at the edge. In the reference battery that
low-rate cell was the only one whose short-warmed windows failed the settling check, while its
15-minute window was its most stable. When measuring a low-throughput service, warm longer.

The rate is chosen per cell so the uninstrumented application uses 0.6-0.95 of a core (under
half of its two vCPUs). Two reasons. A pod at its CPU ceiling queues every microsecond
of extra work into waiting time and measures the ceiling, not the agent. And on a
hyperthreaded core CPU per request is only linear in the work per request while the sibling
thread is mostly idle: above roughly 1.1 cores both siblings are busy most of the time,
per-thread throughput drops, and any extra work - the agent's included - is amplified. Every
cell therefore runs below that knee.

## 3. Isolation

- One namespace per cell with its own Postgres, Redis and echo service, each cell on its own
  application node and its own dependency node. No figure is ever compared across cells.
- Application pods are Guaranteed QoS (requests = limits = 2 CPUs) on nodes running the kubelet
  static CPU manager, so the pod owns two CPUs exclusively. With `full-pcpus-only` those two
  CPUs are the two hyperthreads of one physical core, which is how the reference runs were
  configured. On Kubernetes 1.33 and later the kubelet also drops the CFS quota for such pods
  (`cpu.max = max`); on earlier versions a quota equal to the 2-CPU limit remains, which cannot
  throttle a pod confined to 2 CPUs. Either is accepted; a quota smaller than the cpuset is not.
  `infra/verify-nodes.sh` refuses to run if the static policy is not in effect, and every arm
  records `cpu.max`, `cpuset.cpus.effective` and the throttled-period delta.
- kubelet, the container runtime, and Odigos' node agent and collector run on CPUs outside the
  application's exclusive set, so the agent's node-level CPU never lands on the application's
  cores.
- Non-burstable VM types only. Burstable types (AWS t-family, Azure B-series) measure their own
  credit balance.
- The load generator, the databases and the trace sink never share a node with an application
  under measurement. No port-forward anywhere in the load or export path.

## 4. Pairing and resolution

Every comparison is made inside one process. Odigos attaches to the running test application in
seconds without a restart, so each repetition starts the application once and then measures four
windows on that same process:

    off  ->  off  ->  100  ->  100

Two processes started from the identical image differ by several percent in CPU per request
depending on how the JIT compiler happened to optimise each, which is larger than the effect
being measured, so no comparison ever crosses process instances.

The two uninstrumented windows are averaged to give that process's baseline, and the gap between
them is that repetition's own resolution: on the reference cluster it averaged about 2% of
baseline. The two instrumented windows are averaged the same way. Overhead for the repetition
is the difference, and the published figure for a cell is the mean over repetitions with a
95% t-interval. Six repetitions were run.

No uninstrumented window is ever taken after the agent has attached, since removing
instrumentation from a running process is not guaranteed to return it to its original state.

Each repetition restarts the application and its backends (`Recreate`), asserts the database
row counts and resets the Redis keyspace. The only table written to is a pre-filled ring
updated in place, so the dataset does not grow over a long battery.

## 5. Validity gates (per window)

| Rule | Why |
|---|---|
| error rate at most 0.1% | a timeout-bound window measures the timeout, not the work |
| max latency below the client timeout | same defect from the latency side: requests parked, not served |
| dropped iterations at most 0.5% | the load generator, not the workload, was the bottleneck |
| achieved rate at least 99% of offered | otherwise CPU per request is read at a different operating point |
| throttled share of the window at most 0.1% | a throttled container measures its quota |
| QoS Guaranteed, a 2-CPU cpuset, no CFS quota smaller than it | the static policy must actually have applied |
| no pod restart or pod change during the window | a restarted process is cold |
| `\|h1 - h2\| / mean` at most 5% | the two halves of the window must agree, or the process was still settling |
| JIT compilation at most 1000 ms per minute in the window | a cold process compiles seconds per minute |
| instrumented window has a delivered `InstrumentationConfig`; `off` window has no Source | proves what was measured, rather than what was intended |
| per-request checks pass (operations executed and `cpu_units` match the design) | the application did the work the cell claims |
| instrumented window: spans per request within 10% of design | coverage is what the model assumes |

A refused window is recorded with its reasons and left out; it is never averaged in.

## 6. Throughput and capacity

With CPU headroom a service loses no throughput; spare capacity absorbs the additional work.
Every arm in this study delivers its full offered rate. At saturation a service gives up
exactly the CPU it gained: if CPU per request rises 3%, peak throughput falls about 3%. The
throughput question therefore reduces to the CPU question, and the report states capacity
impact as the CPU-per-request delta, applicable only to a service running at 100% CPU.

## 7. What every arm retains

`k6.json` (percentiles, error rate, dropped iterations, check failures), `cpu.json`,
`cpu.h1.json`, `cpu.h2.json`, `cg.start/mid/end` (raw cgroup snapshots), `sink.json`
(spans accepted at the collector), `jvm.json` (the runtime's GC pause, allocation, compilation, heap
high-water), `probe.json` (odiglet / node collector / node-root CPU), `gate.json` (decision
and reasons), `meta.json` (pod, node, QoS, cpuset, cpu.max, rate, order), `ic.yaml` (the
resolved instrumentation configuration as delivered). Every figure in `RESULTS.md` is derived
from these files by `analysis/analyze.py` and `analysis/validate.py`, and can be recomputed.
