# Pre-registered predictions

Committed **before** the validation run produces any data. Nothing here is
derived from the run it predicts.

*Revision 2, 20 September 2026. Revision 1 used a span sweep at a single CPU
level; it measured the per-span cost well but its cells did not correspond to
any published bucket, so their results could not be compared against the
published ranges. This revision reproduces three buckets exactly and keeps two cells for
the per-span cost. No measurement had been taken when either revision was
written.*

## What is being tested

The bucket model groups applications into buckets by CPU per request and gives each
bucket an expected overhead and a range. Those figures come from one measured
constant: Odigos costs **46 µs of CPU per span**, measured range 40 to 52 µs.

Two claims are tested, and they need different cells.

**Claim 1 - a bucket's published range holds.** Three cells are configured to be
a bucket: its CPU per request, and the median span count measured for
applications in that bucket in production tracing data. The measured overhead
should land inside the range published for the bucket.

**Claim 2 - the per-span cost is 46 µs and does not depend on the request.** Two
cells carry 20 spans, one on a 3.5 ms request and one on a 14 ms request. Their
signal is large enough to measure precisely, and they must agree on cost per span.
The buckets whose predicted effect is too small to measure directly rest on this.

## Predictions

| Cell | Role | CPU/req | Spans | Predicted overhead | Published range |
|---|---|---|---|---|---|
| s | bucket "under 2 ms" | 1.5 ms | 1 | **3.07%** | 2.6 - 17.2% |
| schatty | bucket "2 to 4 ms" | 3.0 ms | 2 | **3.07%** | 1.3 - 9.2% |
| m | bucket "4 to 8 ms" | 6.0 ms | 2 | **1.53%** | 0.9 - 3.9% |
| l | per-span cost | 3.5 ms | 20 | **26.29%** | not a bucket |
| xl | per-span cost, 4x CPU | 14 ms | 20 | **6.57%** | not a bucket |

Added CPU per request, which is what is actually measured:

| Cell | Predicted added CPU | At 40 - 52 µs per span |
|---|---|---|
| s | 0.046 ms | 0.040 - 0.052 ms |
| schatty | 0.092 ms | 0.080 - 0.104 ms |
| m | 0.092 ms | 0.080 - 0.104 ms |
| l | 0.920 ms | 0.800 - 1.040 ms |
| xl | 0.920 ms | 0.800 - 1.040 ms |

## What counts as a failure

- Any of the three bucket cells measuring an overhead outside its published range.
- A per-span cost from cells `l` or `xl` outside 40 to 52 µs.
- Cells `l` and `xl` disagreeing on cost per span by more than their intervals allow,
  which would mean the cost depends on the request's own CPU.
- A fixed per-request cost above 0.1 ms once the span count is accounted for.

## What this run cannot do

The rig resolves a change of roughly 2% of the baseline CPU per request. A bucket
is therefore directly measurable only where `spans > 0.43 x CPU-ms`. That holds
for the two thinnest buckets, is marginal for `4 to 8 ms`, and is false for the
rest: their predicted effect is smaller than the run-to-run variation of the process.

Cell `m` is included knowing this. Its predicted 0.092 ms sits near the
resolution limit, so it may return a wide interval rather than a sharp number.
That is a property of the effect being small, not of the method, and the result
will be reported either way.

The four heavier buckets are not measured here. They rest on claim 2: the same
constant over a larger denominator.

## Method

Five cells, each on a dedicated node, application pod pinned to exclusive
physical cores. Open-loop load at a fixed arrival rate, sized so the
uninstrumented pod runs near 0.9 of its two vCPUs. Each repetition runs four
arms on one process without restarting it - uninstrumented, uninstrumented,
instrumented, instrumented - so no comparison crosses processes. CPU comes from
the container's own cgroup counter. Six repetitions per cell.

An arm is discarded, never adjusted, if errors exceed 0.1%, the load generator
misses its rate, the container is throttled or loses its exclusive cores, the process
restarts, the two halves of the window disagree, or the delivered span count is
not the one designed.
