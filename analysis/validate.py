#!/usr/bin/env python3
"""Compare a validation run against PREDICTIONS.md.

    python3 analysis/validate.py results/v1 [results/v2 ...]

Reads the accepted arms directly from each run directory, pairs the
uninstrumented and instrumented windows within a JVM, and prints the measured
overhead beside the prediction and, for the bucket cells, beside the range the
report publishes. Written before the run produced any data.

Amended once after the run began, before any run produced a result, to add the
"pred(meas)" column. The pre-registered constant and cell specs are untouched;
the new column applies that same locked 46 us to the baseline CPU and span count
the run actually measured, instead of to the design targets the cell was
calibrated toward. It is needed because a cell that lands a few percent off its
CPU target is not a failure of the model -- the model's inputs are the real
baseline and the real span count. "pred(design)" is kept as the pre-registered
figure, and the bucket verdicts still test measured overhead against the
published band, so nothing that was pre-registered is affected.
"""
import json, math, os, statistics as st, sys

# Locked to PREDICTIONS.md revision 2. Do not edit after the run.
C_SPAN_MS = 0.046
CELLS = {
    's':       dict(role='bucket "under 2 ms"', cpu=1.5, spans=1,  lo=2.6, hi=17.2),
    'schatty': dict(role='bucket "2 to 4 ms"',  cpu=3.0, spans=2,  lo=1.3, hi=9.2),
    'm':       dict(role='bucket "4 to 8 ms"',  cpu=6.0, spans=2,  lo=0.9, hi=3.9),
    'l':       dict(role='per-span cost',       cpu=3.5, spans=20, lo=None, hi=None),
    'xl':      dict(role='per-span cost, 4x CPU', cpu=14.0, spans=20, lo=None, hi=None),
}
# Bucket edges, in CPU ms per request, for the three cells that claim a bucket.
# Derived from the role strings above; kept separate so the locked block is not
# edited. A cell whose measured baseline leaves its bucket cannot be compared
# against that bucket's published band, and says so.
EDGES = {'s': (0.0, 2.0), 'schatty': (2.0, 4.0), 'm': (4.0, 8.0)}

T = {1: 12.71, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
     8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179}


def load(dirs):
    """cell -> list of per-repetition (baseline, instrumented, spans) triples."""
    out = {}
    for d in dirs:
        for cell in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            pass
    for d in dirs:
        summ = os.path.join(d, 'summary.json')
        if not os.path.exists(summ):
            continue
        s = json.load(open(summ))
        for cell, c in s.get('cells', {}).items():
            for rep in c.get('reps', []):
                off = [a['cpu_ms_per_req'] for a in rep['arms'] if a['arm'] == 'off' and a['accepted']]
                on = [a['cpu_ms_per_req'] for a in rep['arms'] if a['arm'] == '100' and a['accepted']]
                sp = [a['spans_per_req'] for a in rep['arms'] if a['arm'] == '100' and a['accepted']]
                if off and on:
                    out.setdefault(cell, []).append((st.mean(off), st.mean(on), st.mean(sp) if sp else None))
    return out


def ci(vals):
    n = len(vals)
    if n < 2:
        return (st.mean(vals), float('nan')) if n else (float('nan'), float('nan'))
    sd = st.stdev(vals)
    return st.mean(vals), T.get(n - 1, 1.96) * sd / math.sqrt(n)


def main(dirs):
    data = load(dirs)
    if not data:
        print('no summary.json found in: ' + ', '.join(dirs)); return 1
    print('VALIDATION AGAINST PRE-REGISTERED PREDICTIONS (PREDICTIONS.md rev 2)')
    print('per-span cost assumed: %.0f us   runs: %s\n' % (C_SPAN_MS * 1000, ', '.join(dirs)))
    print('%-9s %-22s %4s %9s %7s %19s %11s %11s  %s' % (
        'cell', 'role', 'reps', 'baseline', 'spans', 'measured overhead',
        'pred(design)', 'pred(meas)', 'verdict'))
    verdicts = []
    for cell, spec in CELLS.items():
        reps = data.get(cell, [])
        if not reps:
            print('%-9s %-24s %4s %9s' % (cell, spec['role'], 0, 'no data')); continue
        base, _ = ci([r[0] for r in reps])
        deltas = [r[1] - r[0] for r in reps]
        d, dhw = ci(deltas)
        spans = st.mean([r[2] for r in reps if r[2]]) if any(r[2] for r in reps) else spec['spans']
        ov, ovhw = 100 * d / base, 100 * dhw / base
        pred = 100 * spec['spans'] * C_SPAN_MS / spec['cpu']
        pred_m = 100 * spans * C_SPAN_MS / base
        if spec['lo'] is not None:
            blo, bhi = EDGES[cell]
            if not (blo <= base < bhi):
                v = 'INVALID: baseline %.2fms is outside the %g-%gms bucket' % (base, blo, bhi)
            else:
                inside = spec['lo'] <= ov <= spec['hi']
                v = 'IN RANGE' if inside else 'OUTSIDE %.1f-%.1f%%' % (spec['lo'], spec['hi'])
        else:
            us = d / spans * 1000
            us_lo, us_hi = (d - dhw) / spans * 1000, (d + dhw) / spans * 1000
            v = '%.0f us/span [%.0f, %.0f]' % (us, us_lo, us_hi)
            v += ' OK' if 40 <= us <= 52 else ' OUTSIDE 40-52'
        verdicts.append((cell, v))
        print('%-9s %-22s %4d %8.2fms %7.2f %9.2f%% +-%-6.2f %10.2f%% %10.2f%%  %s' % (
            cell, spec['role'][:22], len(reps), base, spans, ov, ovhw, pred, pred_m, v))
    # per-span cost across every cell, and the intercept
    def _spans(c):
        m = [r[2] for r in data[c] if r[2]]
        return st.mean(m) if m else CELLS[c]['spans']
    pts = [(_spans(c), st.mean([r[1] - r[0] for r in data[c]])) for c in data if c in CELLS]
    if len(pts) >= 3:
        n = len(pts); sx = sum(p[0] for p in pts); sy = sum(p[1] for p in pts)
        sxx = sum(p[0] ** 2 for p in pts); sxy = sum(p[0] * p[1] for p in pts)
        den = n * sxx - sx * sx
        if den:
            slope = (n * sxy - sx * sy) / den; icpt = (sy - slope * sx) / n
            print('\nfit across all cells: added CPU = %.4f ms + %.1f us per span' % (icpt, slope * 1000))
            print('  slope %s 40-52 us   intercept %s 0.1 ms' % (
                'inside' if 0.040 <= slope <= 0.052 else 'OUTSIDE', 'under' if abs(icpt) < 0.1 else 'OVER'))
    print('\n' + '\n'.join('  %-9s %s' % v for v in verdicts))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:] or ['results/battery']))
