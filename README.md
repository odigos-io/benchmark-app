# benchmark-app

A test application and Kubernetes harness for benchmarking the CPU overhead of Odigos
Enterprise, and for checking an overhead estimate against a real measurement.

The application lets you dial in two things independently: how much CPU each request
burns, and how many instrumented operations (spans) each request performs. That puts it
anywhere on the overhead curve, so it can stand in for any class of service you want to
test. The harness runs it with and without Odigos on the same running process and reports
the difference.

## The model being tested

Odigos adds CPU per request roughly in proportion to the spans that request produces.
The application's own CPU per request is the denominator:

```
overhead % = CPU Odigos adds per request / CPU the application uses per request
```

So the same agent costs a very different percentage depending on the service: a thin
request with little CPU of its own sees a larger percentage than a heavy request making
the same calls.

## Results

Three applications were configured to match three buckets of the model, and measured
with and without Odigos on AWS, six repetitions each:

| bucket (CPU per request) | expected | measured | predicted range |
|---|---|---|---|
| under 2 ms | 4.9% | 3.4% | 2.6% – 17.2% |
| 2 to 4 ms | 3.1% | 4.5% | 1.3% – 9.2% |
| 4 to 8 ms | 1.9% | 2.2% | 0.9% – 3.9% |

*Expected* is the model's typical overhead for applications in the bucket; the *predicted
range* is where it expects 8 in 10 of them to land.

All three landed inside their predicted range. Throughput held its target rate with zero
errors and median latency moved by about a tenth of a millisecond. Full results, method
and caveats are in [RESULTS.md](RESULTS.md); the per-window data is in
[`results/reference-run.csv`](results/reference-run.csv).

## Running it yourself

| | |
|---|---|
| [SETUP.md](SETUP.md) | Preparing a Kubernetes cluster (any provider), building the images, installing Odigos |
| [VALIDATION.md](VALIDATION.md) | Running the test, reading the results, and running the same test on your own application |
| [METHODOLOGY.md](METHODOLOGY.md) | Why the harness measures the way it does, and every validity check it applies |
| [app/README.md](app/README.md) | The application: what a request does and every configuration knob |

The predictions were written and committed before any data existed:
[PREDICTIONS.md](PREDICTIONS.md), [PRE-DATA-POWER.md](PRE-DATA-POWER.md) and
[ANALYSIS-PLAN.md](ANALYSIS-PLAN.md).

## Layout

```
app/          the application under test
downstream/   a small Go echo service the application calls over HTTP
k6/           the load script (fixed arrival rate)
k8s/          manifests: one namespace per cell, the trace sink, the in-cluster runner
bin/          run, calibrate, and the in-cluster orchestrator driver
infra/        Odigos install and node verification; infra/aws/ is a worked EKS example
analysis/     analysis, validation against the predictions, figures, power analysis
cells.env     the five cells: rate, CPU target and span shape of each
results/      per-window data from the reference run
```
