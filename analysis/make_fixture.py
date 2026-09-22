#!/usr/bin/env python3
"""Synthetic same-JVM runs with a KNOWN delta and a KNOWN drift.

Builds run dirs whose arms carry a planted per-span cost and a planted linear
warm-up drift, so the analyzer's paired delta and drift correction can be
checked against the truth. Usage: mkfixture.py <outdir> [--drift MS_PER_HOUR]
"""
import json, os, random, sys

OUT = sys.argv[1]
DRIFT_MS_PER_HOUR = float(sys.argv[sys.argv.index('--drift') + 1]) if '--drift' in sys.argv else 0.0
BASELINE_NOISE_MS = 0.0 if '--drift' in sys.argv else 0.015
C_SPAN_MS = 0.075           # planted: 75 us per span
NOISE_MS = 0.01             # per-window measurement noise
# Between-window variation of an uninstrumented JVM, as a fraction of its
# CPU per request. Measured on the reference cluster: 1-2%, sign not
# consistent. --drift adds a systematic trend on top for the other shape.
CELLS = {                   # cell: (baseline_ms, spans, rate)
    's': (3.5, 8, 250), 'schatty': (4.4, 14, 200), 'm': (12.0, 8, 75),
    'l': (30.0, 8, 30), 'xl': (60.0, 8, 15),
}
ARMS = ['off', 'off', '100', '25']
ARM_MIN = {0: 40, 1: 20, 2: 20, 3: 20}   # minutes each arm occupies
random.seed(7)


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as fh:
        json.dump(obj, fh)


def build(run, reps_seed):
    random.seed(reps_seed)
    t = 1_700_000_000_000_000_000
    for i, arm in enumerate(ARMS):
        slot = f'{i + 1:02d}-{arm}'
        t += ARM_MIN[i] * 60 * 10 ** 9
        for cell, (base, spans, rate) in CELLS.items():
            hours = (t - 1_700_000_000_000_000_000) / 3.6e12
            drift = DRIFT_MS_PER_HOUR * hours
            add = 0.0 if arm == 'off' else C_SPAN_MS * spans * (1.0 if arm == '100' else 0.55)
            wobble = random.gauss(0, BASELINE_NOISE_MS * base) if BASELINE_NOISE_MS else 0.0
            cpu = base + drift + wobble + add + random.gauss(0, NOISE_MS)
            d = os.path.join(run, slot)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{cell}.cg.start'), 'w') as fh:
                fh.write(f'TS={t}\nusage_usec 1\n')
            write(os.path.join(d, f'{cell}.cpu.json'),
                  {'cpu_ms_per_req': cpu, 'cpu_cores_avg': cpu * rate / 1000,
                   'window_s': 600, 'requests': rate * 600, 'nr_throttled_delta': 0,
                   'cpu_max': 'max 100000', 'cpuset': '1,5', 'cpuset_size': 2, 't0_ns': t})
            for half, off in (('h1', -0.02), ('h2', 0.02)):
                write(os.path.join(d, f'{cell}.cpu.{half}.json'), {'cpu_ms_per_req': cpu + off})
            write(os.path.join(d, f'{cell}.k6.json'),
                  {'rate': rate, 'overall': {'requests': rate * 600, 'tps': rate, 'avg_ms': 20.0,
                   'med_ms': 20.0, 'p95_ms': 22.0, 'p99_ms': 25.0, 'failed_rate': 0,
                   'dropped_iterations': 0, 'check_fail': 0}})
            write(os.path.join(d, f'{cell}.gate.json'), {'ok': True, 'reasons': []})
            write(os.path.join(d, f'{cell}.meta.json'),
                  {'cell': cell, 'preset': cell.upper(), 'arm': arm, 'slot': slot,
                   'pod': f'bucket-app-{cell}-{reps_seed}', 'pod_end': f'bucket-app-{cell}-{reps_seed}',
                   'node': f'node-{cell}', 'qos': 'Guaranteed', 'restarts_start': 0, 'restarts_end': 0,
                   'arrival_rate': rate, 'expect_spans': spans, 'restart': 'once', 'reset_http': 200})
            write(os.path.join(d, f'{cell}.sink.json'),
                  {'spans_per_req': 0.0 if arm == 'off' else (spans if arm == '100' else spans * 0.26),
                   'keep_rate': 0.0 if arm == 'off' else (1.0 if arm == '100' else 0.26)})
            write(os.path.join(d, f'{cell}.jvm.json'),
                  {'compile_ms_per_min': 120, 'gc_pause_pct': 0.2, 'heap_used_mb_max': 500,
                   'thread_cpu_ms_per_req': cpu * 0.95, 'alloc_mb_per_req': 0.5})
            write(os.path.join(d, f'{cell}.probe.json'),
                  {'odiglet_cores': 0.03, 'node_collector_cores': 0.01, 'root_cores_avg': 1.0})
            with open(os.path.join(d, f'{cell}.ic.yaml'), 'w') as fh:
                if arm == 'off':
                    fh.write('items: []\n')
                else:
                    fh.write('kind: InstrumentationConfig\nagentEnabled: true\n')
                    if arm != '100':
                        fh.write(f'percentageAtMost: {arm}\n')


for n in range(1, 5):
    build(os.path.join(OUT, f'f{n}'), n)
print(f'planted: c_span {C_SPAN_MS * 1000:.0f} us/span at 100%, '
      f'{C_SPAN_MS * 1000 * 0.55:.0f} at 25%; drift {DRIFT_MS_PER_HOUR} ms/hour; '
      f'between-window noise {BASELINE_NOISE_MS * 100:.1f}% of baseline')
for cell, (base, spans, _) in CELLS.items():
    print(f'  {cell:8s} true delta @100% = {C_SPAN_MS * spans * 1000:+.0f} us '
          f'({C_SPAN_MS * spans / base * 100:+.2f}%)   @25% = {C_SPAN_MS * spans * 550:+.0f} us')
