# Running the validation

There are two ways to use this. **Option A** runs the test application from this repository
and reproduces the reference battery. **Option B** runs the same before/after measurement on
one of your own applications, needs no code from here, and tests your real workload rather
than a synthetic one.

---

## Option A: the test application

Complete [SETUP.md](SETUP.md) first. Everything below runs through the in-cluster orchestrator,
so your workstation can sleep or disconnect while a run is going.

### The five cells

Three cells reproduce a bucket of the overhead model: the bucket's CPU per request, and the
median span count of applications in that bucket. Two carry a deliberately high span count, so
the per-span cost can be measured where it is large enough to see; the second repeats the first
at four times the CPU per request.

| cell | role | CPU/req target | spans | JDBC / Redis / HTTP | rate |
|---|---|---|---|---|---|
| s | bucket "under 2 ms" | 1.5 ms | 1 | 0 / 0 / 0 | 500/s |
| schatty | bucket "2 to 4 ms" | 3.0 ms | 2 | 1 / 0 / 0 | 300/s |
| m | bucket "4 to 8 ms" | 6.0 ms | 2 | 1 / 0 / 0 | 150/s |
| l | per-span cost | 3.5 ms | 20 | 8 / 5 / 3 | 199/s |
| xl | per-span cost, 4x CPU | 14 ms | 20 | 8 / 5 / 3 | 58/s |

Spans per request are `1 server + JDBC + Redis + 2 x HTTP`: in this application Odigos emits two
client spans for each outbound HTTP call (the HTTP client library's, and the connection's
underneath it). The cells are
defined in [`cells.env`](cells.env).

### 1. Calibrate the CPU knob for your hardware

CPU per request is set in work units, and what a unit costs depends on the processor. The units
in `cells.env` are calibrated for the reference nodes and **will not land on target on other
hardware**. Calibration measures the real cost on each cell's own node under load and works out
the units that hit the target:

```sh
bin/incluster.sh calibrate cal CELLS="s schatty m l xl" WARM=4m DUR=5m
bin/incluster.sh status cal                # follow progress; about 30 minutes per cell
```

Cells are calibrated one after another. For each, the log ends with the fitted line and a
command like:

```
kubectl -n cell-m set env deploy/bucket-app BENCH_CPU_UNITS=146
```

Run that line for every cell, then wait for the deployments to roll:

```sh
for c in s schatty m l xl; do kubectl -n cell-$c rollout status deploy/bucket-app; done
```

**Expect the highest load point of a calibration to be refused.** It deliberately runs the pod
harder than the test does, past the point where both hyperthreads of its core are busy, and the
validity checks are supposed to reject it.

### 2. Run the battery

Six repetitions, all five cells in parallel, about eight hours:

```sh
bin/incluster.sh stop                      # make sure nothing else is running
ARGS="ARMS=off off 100 100 RESTART=once WARM_FIRST=15m WARM=6m DUR=10m"
bin/incluster.sh chain "v1:$ARGS" "v2:$ARGS" "v3:$ARGS" "v4:$ARGS" "v5:$ARGS" "v6:$ARGS"
bin/incluster.sh status                    # check in whenever you like
```

Each repetition restarts the application once, then measures four 10-minute windows on that
same process: without Odigos, without Odigos, with Odigos, with Odigos. Odigos is enabled on the
running process - no restart - so every comparison is made inside one process. The first window
warms for 15 minutes, the others for 6. [METHODOLOGY.md](METHODOLOGY.md) explains each choice.

The status log marks every window ACCEPTED or REJECTED with the reason. An occasional rejection
is normal and expected; the window is left out, never averaged in.

### 3. Fetch the results

**Fetch before touching the orchestrator.** Its working directory lives only as long as the pod,
so deleting or restarting it loses every result.

```sh
for v in v1 v2 v3 v4 v5 v6; do bin/incluster.sh fetch $v; done
```

### 4. Read the results

Combine the six repetitions, then compare against the predictions:

```sh
mkdir -p results/battery
python3 analysis/analyze.py results/v1 results/v2 results/v3 results/v4 results/v5 results/v6 \
        --json results/battery/summary.json > results/battery/analyze.txt
python3 analysis/validate.py results/battery
python3 analysis/validation_figures.py results/battery --out results/battery/figures
python3 analysis/export_csv.py results/v1 results/v2 results/v3 results/v4 results/v5 results/v6 \
        > results/battery/windows.csv
python3 analysis/summarize.py results/battery/windows.csv
```

Pass the combined `results/battery` directory to `validate.py`, not the six run directories:
only the combined summary holds all six repetitions.

`validate.py` prints, per cell, the baseline CPU per request, the spans per request Odigos
actually delivered, the measured overhead with its 95% confidence interval, the prediction,
and the verdict against the bucket's range. It also fits added CPU against span count across
all cells. `summarize.py` prints the same overhead figures plus throughput, errors, dropped
requests and latency percentiles with and without Odigos, in the same format as
[RESULTS.md](RESULTS.md) - run it on `results/reference-run.csv` to see the reference run.

How to read it:

- **Judge the three bucket cells together.** At the noise level of this kind of measurement,
  one cell landing outside its range happens often even when the model is exactly right -
  [PRE-DATA-POWER.md](PRE-DATA-POWER.md) quantifies how often. A consistent pattern across
  cells is what would mean something.
- **Note the direction of any miss.** Below the range means Odigos cost less than predicted;
  above means more.
- **A cell's baseline must fall inside its bucket.** If calibration missed and a cell's
  measured CPU per request left its bucket, `validate.py` says so instead of giving a verdict.

---

## Option B: your own application

This needs nothing from this repository. Pick a service you can put under steady synthetic load
for an hour or two.

**1. Drive it at a fixed request rate.** Use an arrival-rate executor (k6
`constant-arrival-rate`, Gatling `constantUsersPerSec`, or similar), not a fixed number of
virtual users. Pick a rate that holds the pod at roughly half its CPU limit.

**2. Measure what the service costs today.** Warm up until CPU per request is steady. Runtimes
with a just-in-time compiler need tens of thousands of requests through the main path before
they settle, so low-traffic services need longer. Then measure over 10 minutes:

```
CPU per request (ms) = CPU cores used x 1000 / requests per second
```

Take cores from the container's own CPU counter over exactly that window - for example
`rate(container_cpu_usage_seconds_total[10m])` from cAdvisor, or the pod's `cpu.stat`.
`kubectl top` is not precise enough for this.

**3. Enable Odigos on the running pod.** Where Odigos can instrument the service without a
restart, do it that way, so both measurements come from the same process; let it settle for a few
minutes. Where enabling Odigos restarts the pod, the two measurements come from different
processes, which adds a few percent of process-to-process variation: warm the new pod the same
way as in step 2, and repeat the whole comparison more times to average that variation out.

**4. Measure again**, at the same rate, for the same length of window.

**5. Read the span count.** Odigos reports spans per request for the service.

**6. Compare.**

```
added CPU per request = CPU per request with Odigos - CPU per request without
overhead              = added CPU per request / CPU per request without
```

Then check the result against the bucket the service falls in, by its CPU per request.

### Things that will throw the result off

- **Restarting between the two measurements.** Two processes started from the same image can
  differ by a few percent in CPU per request depending on how the runtime optimised each - more
  than the effect you are measuring. Keep both measurements on one process where you can.
- **A pod near its CPU limit.** A throttled container reports lower CPU and higher latency, and
  neither means what it usually does. Keep well under the limit, and check the container's
  throttled-period counter is zero.
- **A fixed number of virtual users.** Throughput then equals users divided by latency, so any
  latency change is counted twice.
- **Too short a warm-up.** Start-up work such as JIT compilation is CPU, so an under-warmed
  measurement charges it to the requests.
- **A single run.** Repeat at least three times. A single run can't reliably resolve a
  difference below about 2% of the service's CPU per request.

---

## Measuring latency and throughput

Both are side effects of CPU, and both are measured from the load generator, on a different node
from the application, so they include the network path a caller sees.

### Latency

Compare latency percentiles at the same fixed request rate, with the service well under its CPU
limit - the same conditions as the CPU measurement, and ideally the same windows.

- **Compare like with like.** The first window after a restart, or after Odigos attaches, carries
  a start-up tail that mostly shows in the high percentiles. In the reference run the p99 of the
  first uninstrumented window was 2.1 and 3.2 times that of the second on two cells, which would
  make Odigos look like it improved p99. Compare the second window without Odigos with the second
  window with it, or leave out the first window of each phase.
- **Use percentiles, not averages.** Report p50, p95 and p99. An average hides a stall that hits a
  small share of requests.
- **Expect the median to rise by about the CPU added per request.** With headroom, a request pays
  for the agent's work on its own thread and nothing more. A median increase much larger than the
  added CPU means the service is queuing, which usually means it is closer to its CPU limit than
  intended.
- **Treat p99 over a single 10-minute window as noise-prone.** It rests on the slowest 1% of
  requests, where garbage collection and other pauses dominate. Judge it across repetitions, and
  look for a consistent direction rather than any single difference.

### Throughput

In a fixed-rate test, throughput is set by the load generator, so it is not a result. What the
test does show is whether the service **kept up**: achieved rate equal to the offered rate, no
errors, and no dropped requests (in k6, `dropped_iterations` counts requests the generator could
not start because earlier ones had not finished). If all three hold with Odigos on, Odigos cost
the service no throughput at that load.

What Odigos costs a service **at its CPU limit** - its maximum throughput - is a separate test:

1. With the service warmed and Odigos off, run a series of fixed-rate tests at increasing rates,
   for example 5 minutes each in steps of 10% of the expected maximum.
2. The service's capacity is the highest rate at which it still keeps up: achieved rate equal to
   offered, no errors, no dropped requests, and p95 latency within your service level objective.
3. Enable Odigos on the same process, warm it, and repeat the same series.
4. Compare the two capacities. Repeat the whole comparison at least three times.

A CPU-bound service at its limit gives up roughly the CPU it gains: if Odigos adds x% to CPU per
request, maximum throughput falls by about x / (1 + x), so a 3% overhead costs about 2.9% of
peak throughput. Treat this as an approximation - near saturation, CPU per request often rises
for reasons of its own, such as contention between hyperthreads - and use the measured
capacities where they differ.

A fixed-rate step in k6 (merge into the options of your own script):

```js
export const options = {
  scenarios: {
    step: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE),  // requests per second for this step
      timeUnit: '1s',
      duration: '5m',
      preAllocatedVUs: 200,
      maxVUs: 2000,
    },
  },
};
```

Run it once per step (`k6 run -e RATE=400 step.js`, then 440, 480, ...) and read
`http_req_duration` percentiles, `http_req_failed` and `dropped_iterations` from each summary.
Never use a fixed number of virtual users for this: throughput then equals users divided by
latency, and a latency change is counted twice.
