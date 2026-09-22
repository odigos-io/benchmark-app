#!/usr/bin/env python3
"""How often does this rig return the right verdict when the model is exactly true?

    python3 analysis/power.py [--reps 6] [--trials 20000] [--noise-pct 1.9]

Simulates the battery with the per-span cost planted at the published 46 us and
the between-arm noise the rig actually shows, then applies validate.py's own
statistics. A cell that fails its check often even when the model holds cannot
be read as evidence against the model when it fails on real data; this prints
that rate per cell, before the real data is looked at.
"""
import random, statistics as st, sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from validate import CELLS, C_SPAN_MS, T

argv = sys.argv[1:]
def opt(n, d): return type(d)(argv[argv.index(n) + 1]) if n in argv else d
REPS   = opt('--reps', 6)
TRIALS = opt('--trials', 20000)
NOISE  = opt('--noise-pct', 1.9) / 100.0

# baseline CPU per request measured in arm 01-off of the battery
BASE = {'s': 1.534, 'schatty': 2.901, 'm': 6.108, 'l': 3.225, 'xl': 13.590}
random.seed(11)


def ci(vals):
    n = len(vals)
    return st.mean(vals), T.get(n - 1, 1.96) * st.stdev(vals) / (n ** 0.5)


print(f'model planted TRUE at {C_SPAN_MS*1000:.0f} us/span; between-arm noise '
      f'{NOISE*100:.1f}% of baseline; {REPS} reps; {TRIALS} simulated batteries\n')
print(f"{'cell':9}{'true':>8}{'effect/SE':>11}{'verdict when model is true':>30}")
for cell, spec in CELLS.items():
    base, spans = BASE[cell], spec['spans']
    true_d = C_SPAN_MS * spans
    sd_arm = NOISE * base
    sd_delta = sd_arm            # mean of 2 off and 2 on arms -> sd_arm * sqrt(1/2+1/2)
    hits = 0
    for _ in range(TRIALS):
        deltas = [random.gauss(true_d, sd_delta) for _ in range(REPS)]
        d, hw = ci(deltas)
        ov = 100 * d / base
        if spec['lo'] is not None:
            ok = spec['lo'] <= ov <= spec['hi']
        else:
            us = d / spans * 1000
            ok = 40 <= us <= 52
        hits += ok
    label = f'in its published band' if spec['lo'] is not None else 'inside 40-52 us'
    print(f'{cell:9}{100*true_d/base:7.2f}%{true_d/(sd_delta/REPS**0.5):11.1f}'
          f'{100*hits/TRIALS:19.1f}% {label}')
print('\nA low number is a property of the measurement, not of the model.')
