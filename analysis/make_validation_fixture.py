#!/usr/bin/env python3
"""Synthetic battery with a KNOWN per-span cost, in the validation cell shapes.

    python3 analysis/make_validation_fixture.py <outdir> [--us 46] [--noise-pct 2.0] [--reps 6]

Plants a per-span cost and a between-window baseline wobble, then leaves it to
analyze.py and validate.py to recover them. If the validator cannot return the
planted figure from data where the truth is known, its verdict on real data
means nothing. The noise default is the 2% of baseline that two consecutive
uninstrumented arms of the same JVM actually show on this rig.
"""
import json, os, random, sys

argv = sys.argv[1:]
OUT = argv[0] if argv and not argv[0].startswith('--') else 'fixture'
def opt(name, dflt):
    return type(dflt)(argv[argv.index(name) + 1]) if name in argv else dflt
C_SPAN_MS = opt('--us', 46.0) / 1000.0
NOISE_PCT = opt('--noise-pct', 2.0) / 100.0
REPS      = opt('--reps', 6)
WINDOW_NOISE_MS = 0.004

# cell: (baseline_ms, spans, rate) - the shapes actually running in the battery
CELLS = {'s': (1.534, 1, 500), 'schatty': (2.901, 2, 300), 'm': (6.108, 2, 150),
         'l': (3.225, 20, 199), 'xl': (13.590, 20, 58)}
ARMS = ['off', 'off', '100', '100']
ARM_MIN = {0: 25, 1: 16, 2: 16, 3: 16}


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as fh:
        json.dump(obj, fh)


def build(run, seed):
    random.seed(seed)
    t = 1_700_000_000_000_000_000
    # one JVM per repetition: its baseline offset persists across that run's arms
    off_by_cell = {c: random.gauss(0, NOISE_PCT * b) for c, (b, _, _) in CELLS.items()}
    for i, arm in enumerate(ARMS):
        slot = f'{i + 1:02d}-{arm}'
        t += ARM_MIN[i] * 60 * 10 ** 9
        for cell, (base, spans, rate) in CELLS.items():
            add = 0.0 if arm == 'off' else C_SPAN_MS * spans
            wobble = random.gauss(0, NOISE_PCT * base)
            cpu = base + off_by_cell[cell] + wobble + add + random.gauss(0, WINDOW_NOISE_MS)
            d = os.path.join(run, slot)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f'{cell}.cg.start'), 'w') as fh:
                fh.write(f'TS={t}\nusage_usec 1\n')
            write(os.path.join(d, f'{cell}.cpu.json'),
                  {'cpu_ms_per_req': cpu, 'cpu_cores_avg': cpu * rate / 1000,
                   'window_s': 600, 'requests': rate * 600, 'nr_throttled_delta': 0,
                   'cpu_max': 'max 100000', 'cpuset': '1,5', 'cpuset_size': 2, 't0_ns': t})
            for half, o in (('h1', -0.01), ('h2', 0.01)):
                write(os.path.join(d, f'{cell}.cpu.{half}.json'), {'cpu_ms_per_req': cpu + o})
            write(os.path.join(d, f'{cell}.k6.json'),
                  {'rate': rate, 'overall': {'requests': rate * 600, 'tps': rate, 'avg_ms': 20.0,
                   'med_ms': 20.0, 'p95_ms': 22.0, 'p99_ms': 25.0, 'failed_rate': 0,
                   'dropped_iterations': 0, 'check_fail': 0}})
            write(os.path.join(d, f'{cell}.gate.json'), {'ok': True, 'reasons': []})
            write(os.path.join(d, f'{cell}.meta.json'),
                  {'cell': cell, 'preset': cell.upper(), 'arm': arm, 'slot': slot,
                   'pod': f'bucket-app-{cell}-{seed}', 'pod_end': f'bucket-app-{cell}-{seed}',
                   'node': f'node-{cell}', 'qos': 'Guaranteed', 'restarts_start': 0,
                   'restarts_end': 0, 'arrival_rate': rate, 'expect_spans': spans,
                   'restart': 'once', 'reset_http': 200})
            write(os.path.join(d, f'{cell}.sink.json'),
                  {'spans_per_req': 0.0 if arm == 'off' else float(spans),
                   'keep_rate': 0.0 if arm == 'off' else 1.0})
            write(os.path.join(d, f'{cell}.jvm.json'),
                  {'compile_ms_per_min': 120, 'gc_pause_pct': 0.2, 'heap_used_mb_max': 500,
                   'thread_cpu_ms_per_req': cpu * 0.95, 'alloc_mb_per_req': 0.5})
            write(os.path.join(d, f'{cell}.probe.json'),
                  {'odiglet_cores': 0.03, 'node_collector_cores': 0.01, 'root_cores_avg': 1.0})
            with open(os.path.join(d, f'{cell}.ic.yaml'), 'w') as fh:
                fh.write('items: []\n' if arm == 'off'
                         else 'kind: InstrumentationConfig\nagentEnabled: true\n')


for n in range(1, REPS + 1):
    build(os.path.join(OUT, f'v{n}'), n)
print(f'planted {C_SPAN_MS*1000:.0f} us/span; baseline wobble {NOISE_PCT*100:.1f}% of baseline; {REPS} reps')
for cell, (base, spans, _) in CELLS.items():
    print(f'  {cell:8s} true delta = {C_SPAN_MS*spans*1000:+6.0f} us  ({C_SPAN_MS*spans/base*100:+5.2f}%)')
