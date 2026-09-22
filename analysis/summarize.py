#!/usr/bin/env python3
"""Overhead, latency and throughput per cell, from a per-window CSV.

    python3 analysis/summarize.py [results/reference-run.csv]

Works on the reference run's data as published, or on your own run after
analysis/export_csv.py has turned its run directories into the same format.

Uses accepted windows only. For each repetition, the baseline is the mean of its
accepted uninstrumented windows and the instrumented figure the mean of its
accepted instrumented windows, both on the same JVM, and their difference is the
CPU Odigos added. A cell's overhead is the mean added CPU over the mean baseline,
with a 95% t-interval over repetitions - the same definition analysis/validate.py
uses, so the two agree.
"""
import csv, math, statistics as st, sys

T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365}
BANDS = {'s': ('under 2 ms', 2.6, 17.2, 4.9), 'schatty': ('2 to 4 ms', 1.3, 9.2, 3.1),
         'm': ('4 to 8 ms', 0.9, 3.9, 1.9)}


def ci(v):
    if len(v) < 2:
        return st.mean(v), float('nan')
    return st.mean(v), T95.get(len(v) - 1, 1.96) * st.stdev(v) / math.sqrt(len(v))


def main(path):
    rows = [r for r in csv.DictReader(open(path)) if r['accepted'] == 'True']
    cells = {}
    for r in rows:
        cells.setdefault(r['cell'], {}).setdefault(int(r['repetition']), []).append(r)

    print('Overhead per cell (accepted windows, off/on pairs on the same process, 95% CI)\n')
    print(f"{'cell':9}{'baseline':>10}{'spans':>7}{'overhead':>18}   bucket       expected  range")
    for c in ('s', 'schatty', 'm', 'l', 'xl'):
        if c not in cells:
            continue
        added, base, sp = [], [], []
        for rep, ws in sorted(cells.get(c, {}).items()):
            off = [float(w['cpu_ms_per_req']) for w in ws if w['odigos'] == 'off']
            on = [float(w['cpu_ms_per_req']) for w in ws if w['odigos'] == 'on']
            if off and on:
                b, o = st.mean(off), st.mean(on)
                added.append(o - b); base.append(b)
                sp += [float(w['spans_per_req']) for w in ws if w['odigos'] == 'on']
        if not added:
            print(f"{c:9}  no repetition has both an accepted off and on window")
            continue
        d, dhw = ci(added)
        mb = st.mean(base)
        m, hw = 100 * d / mb, 100 * dhw / mb
        line = f"{c:9}{mb:8.2f}ms{st.mean(sp):7.2f}{m:10.2f}% +-{hw:5.2f}"
        if c in BANDS:
            name, lo, hi, exp = BANDS[c]
            line += f"   {name:12} {exp:5.1f}%   {lo}-{hi}%  {'in range' if lo <= m <= hi else 'OUTSIDE'}"
        print(line)

    print('\nLatency and throughput, mean over accepted windows (latency measured at the load generator)\n')
    print(f"{'cell':9}{'odigos':>7}{'req/s':>9}{'errors':>8}{'dropped':>9}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}")
    for c in ('s', 'schatty', 'm', 'l', 'xl'):
        means = {}
        for mode in ('off', 'on'):
            ws = [r for r in rows if r['cell'] == c and r['odigos'] == mode]
            if not ws:
                continue
            f = lambda k: st.mean(float(w[k]) for w in ws)
            means[mode] = {k: f(k) for k in ('p50_ms', 'p95_ms', 'p99_ms')}
            print(f"{c:9}{mode:>7}{f('achieved_rps'):9.1f}{max(float(w['error_rate']) for w in ws):8.3f}"
                  f"{sum(int(w['dropped']) for w in ws):9d}{f('p50_ms'):9.2f}{f('p95_ms'):9.2f}{f('p99_ms'):9.2f}")
        if len(means) == 2:
            d = {k: means['on'][k] - means['off'][k] for k in means['on']}
            print(f"{'':9}{'change':>7}{'':26}{d['p50_ms']:+9.2f}{d['p95_ms']:+9.2f}{d['p99_ms']:+9.2f}")

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'results/reference-run.csv')
