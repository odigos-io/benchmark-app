#!/usr/bin/env python3
"""One CSV row per measurement window, from one or more run directories.

    python3 analysis/export_csv.py results/v1 results/v2 ... > results/battery/windows.csv
    python3 analysis/summarize.py results/battery/windows.csv

Run directories are taken as repetitions, numbered in the order given. The columns
match results/reference-run.csv, so the reference run and yours are summarized by
the same code.
"""
import csv, json, os, sys

CELLS = ('s', 'schatty', 'm', 'l', 'xl')
FIELDS = ['repetition', 'window', 'cell', 'odigos', 'accepted', 'refusal_reason',
          'cpu_ms_per_req', 'half1_cpu_ms', 'half2_cpu_ms', 'spans_per_req', 'requests',
          'achieved_rps', 'p50_ms', 'p95_ms', 'p99_ms', 'error_rate', 'dropped']


def load(path):
    with open(path) as fh:
        return json.load(fh)


def rows(run_dirs):
    for rep, run in enumerate(run_dirs, 1):
        for window in sorted(os.listdir(run)):
            d = os.path.join(run, window)
            if not os.path.isdir(d):
                continue
            for c in CELLS:
                try:
                    cpu = load(f'{d}/{c}.cpu.json')
                    k6 = load(f'{d}/{c}.k6.json')['overall']
                    gate = load(f'{d}/{c}.gate.json')
                    sink = load(f'{d}/{c}.sink.json')
                    h1 = load(f'{d}/{c}.cpu.h1.json')['cpu_ms_per_req']
                    h2 = load(f'{d}/{c}.cpu.h2.json')['cpu_ms_per_req']
                except (OSError, KeyError, ValueError):
                    continue
                yield dict(
                    repetition=rep, window=window, cell=c,
                    odigos='off' if window.endswith('-off') else 'on',
                    accepted=bool(gate.get('ok')), refusal_reason='; '.join(gate.get('reasons', [])),
                    cpu_ms_per_req=round(cpu['cpu_ms_per_req'], 4),
                    half1_cpu_ms=round(h1, 4), half2_cpu_ms=round(h2, 4),
                    spans_per_req=round(sink.get('spans_per_req') or 0, 3),
                    requests=k6['requests'], achieved_rps=round(k6['tps'], 2),
                    p50_ms=round(k6['med_ms'], 3), p95_ms=round(k6['p95_ms'], 3),
                    p99_ms=round(k6['p99_ms'], 3), error_rate=k6['failed_rate'],
                    dropped=k6['dropped_iterations'])


def main(run_dirs):
    if not run_dirs:
        sys.exit(__doc__)
    out = csv.DictWriter(sys.stdout, fieldnames=FIELDS)
    out.writeheader()
    n = 0
    for r in rows(run_dirs):
        out.writerow(r); n += 1
    print(f'{n} windows from {len(run_dirs)} run directories', file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv[1:])
