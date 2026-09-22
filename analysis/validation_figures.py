#!/usr/bin/env python3
"""Figures for the validation report. Numbers come from validate.py's own loader,
so a figure cannot disagree with the table beside it.

    python3 analysis/validation_figures.py results/battery --out figures/

Two figures. The first asks the pre-registered question: does each bucket cell's
measured overhead fall inside the range the report publishes for that bucket.
The second asks whether the per-span constant is what we claimed, on the two
cells carrying enough spans to resolve it.
"""
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import CELLS, EDGES, C_SPAN_MS, load, ci

SURFACE, INK, INK_2, MUTED, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#8a8985', '#e6e6e3'
S1, S2, S3 = '#2a78d6', '#eb6834', '#008300'
BAND = '#dfeccf'


def _o(w, h, label):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{label}"><rect width="{w}" height="{h}" fill="{SURFACE}"/>']


def _leg(x, y, items):
    out = []
    for lab, col, shape in items:
        if shape == 'band':
            out.append(f'<rect x="{x}" y="{y-7}" width="14" height="9" rx="2" fill="{col}"/>')
            w = 18
        elif shape == 'tick':
            out.append(f'<line x1="{x+4}" y1="{y-8}" x2="{x+4}" y2="{y+2}" stroke="{col}" stroke-width="2.5"/>')
            w = 12
        else:
            out.append(f'<circle cx="{x+5}" cy="{y-3}" r="4.5" fill="{col}" stroke="{SURFACE}" stroke-width="2"/>')
            w = 14
        out.append(f'<text x="{x+w}" y="{y+1}" font-size="10" fill="{INK_2}">{lab}</text>')
        x += w + 8 + len(lab) * 5.5
    return out


def bucket_verdict(rows):
    """rows: [(cell, role, measured, halfwidth, predicted, lo, hi)] for bucket cells."""
    W, L, R, T, RH = 640, 132, 30, 46, 46
    H = T + RH * len(rows) + 46
    pw = W - L - R
    hi = max(max(r[6] for r in rows), max(r[2] + r[3] for r in rows)) * 1.10
    # a confidence interval reaching below zero must be visible, not clipped to
    # the axis: an interval containing zero means the effect is not resolved
    lo_ax = min(0.0, min(r[2] - r[3] for r in rows if r[3] == r[3]) * 1.15)
    o = _o(W, H, 'Measured overhead against the published range for each bucket')

    def X(v):
        return L + (max(lo_ax, min(v, hi)) - lo_ax) / (hi - lo_ax) * pw

    for k in range(0, 6):
        v = lo_ax + (hi - lo_ax) * k / 5
        gx = X(v)
        o.append(f'<line x1="{gx:.1f}" y1="{T-12}" x2="{gx:.1f}" y2="{T+RH*len(rows)-14}" '
                 f'stroke="{GRID}" stroke-width="1"/>')
        o.append(f'<text x="{gx:.1f}" y="{T+RH*len(rows)+2}" text-anchor="middle" font-size="10" '
                 f'fill="{MUTED}">{v:.1f}%</text>')

    if lo_ax < 0:
        o.append(f'<line x1="{X(0):.1f}" y1="{T-12}" x2="{X(0):.1f}" y2="{T+RH*len(rows)-14}" '
                 f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="3 3"/>')
    for i, (cell, role, m, hw, pred, lo, up) in enumerate(rows):
        cy = T + RH * i + 6
        o.append(f'<rect x="{X(lo):.1f}" y="{cy-13}" width="{X(up)-X(lo):.1f}" height="26" rx="4" fill="{BAND}"/>')
        o.append(f'<text x="{L-10}" y="{cy-1}" text-anchor="end" font-size="11.5" fill="{INK}" '
                 f'font-weight="600">{role}</text>')
        o.append(f'<text x="{L-10}" y="{cy+12}" text-anchor="end" font-size="10" fill="{MUTED}">cell {cell}</text>')
        o.append(f'<line x1="{X(pred):.1f}" y1="{cy-12}" x2="{X(pred):.1f}" y2="{cy+12}" '
                 f'stroke="{S2}" stroke-width="2.5"/>')
        if hw == hw:  # not NaN
            o.append(f'<line x1="{X(m-hw):.1f}" y1="{cy:.1f}" x2="{X(m+hw):.1f}" y2="{cy:.1f}" '
                     f'stroke="{S1}" stroke-width="2"/>')
            for e in (m - hw, m + hw):
                o.append(f'<line x1="{X(e):.1f}" y1="{cy-5}" x2="{X(e):.1f}" y2="{cy+5}" '
                         f'stroke="{S1}" stroke-width="2"/>')
        o.append(f'<circle cx="{X(m):.1f}" cy="{cy:.1f}" r="5" fill="{S1}" stroke="{SURFACE}" stroke-width="2"/>')
        inside = lo <= m <= up
        o.append(f'<text x="{W-R}" y="{cy-1}" text-anchor="end" font-size="10.5" '
                 f'fill="{S3 if inside else S2}" font-weight="600">{"in range" if inside else "outside"}</text>')
        o.append(f'<text x="{W-R}" y="{cy+12}" text-anchor="end" font-size="10" fill="{MUTED}">'
                 f'{m:.2f}%</text>')

    o += _leg(L, H - 12, [('published range', BAND, 'band'), ('predicted', S2, 'tick'),
                          ('measured, 95% CI', S1, 'dot')])
    return ''.join(o) + '</svg>'


def per_span(rows, lo=40.0, hi=52.0):
    """rows: [(cell, spans, us, halfwidth)]."""
    W, L, R, T, RH = 640, 132, 30, 44, 44
    H = T + RH * len(rows) + 46
    pw = W - L - R
    top = max(max(r[2] + (r[3] if r[3] == r[3] else 0) for r in rows), hi) * 1.15
    bot = min(min(r[2] - (r[3] if r[3] == r[3] else 0) for r in rows), 0.0)
    o = _o(W, H, 'Measured cost per span against the published 40 to 52 microsecond range')

    def X(v):
        return L + (v - bot) / (top - bot) * pw

    o.append(f'<rect x="{X(lo):.1f}" y="{T-10}" width="{X(hi)-X(lo):.1f}" '
             f'height="{RH*len(rows)-8}" rx="4" fill="{BAND}"/>')
    for k in range(0, 6):
        v = bot + (top - bot) * k / 5
        o.append(f'<text x="{X(v):.1f}" y="{T+RH*len(rows)+2}" text-anchor="middle" font-size="10" '
                 f'fill="{MUTED}">{v:.0f}</text>')
    if bot < 0 < top:
        o.append(f'<line x1="{X(0):.1f}" y1="{T-16}" x2="{X(0):.1f}" y2="{T+RH*len(rows)-16}" '
                 f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="3 3"/>')

    for i, (cell, spans, us, hw) in enumerate(rows):
        cy = T + RH * i + 4
        o.append(f'<text x="{L-10}" y="{cy-1}" text-anchor="end" font-size="11.5" fill="{INK}" '
                 f'font-weight="600">cell {cell}</text>')
        o.append(f'<text x="{L-10}" y="{cy+12}" text-anchor="end" font-size="10" fill="{MUTED}">'
                 f'{spans:.0f} spans/req</text>')
        if hw == hw:
            o.append(f'<line x1="{X(us-hw):.1f}" y1="{cy:.1f}" x2="{X(us+hw):.1f}" y2="{cy:.1f}" '
                     f'stroke="{S1}" stroke-width="2"/>')
            for e in (us - hw, us + hw):
                o.append(f'<line x1="{X(e):.1f}" y1="{cy-5}" x2="{X(e):.1f}" y2="{cy+5}" '
                         f'stroke="{S1}" stroke-width="2"/>')
        o.append(f'<circle cx="{X(us):.1f}" cy="{cy:.1f}" r="5" fill="{S1}" stroke="{SURFACE}" stroke-width="2"/>')
        o.append(f'<text x="{W-R}" y="{cy+3}" text-anchor="end" font-size="10.5" fill="{INK}">{us:.0f} us</text>')

    o.append(f'<text x="{L+pw/2:.0f}" y="{H-24}" text-anchor="middle" font-size="11" fill="{INK}">'
             f'CPU added per span, microseconds</text>')
    o += _leg(L, H - 8, [('published 40-52 us', BAND, 'band'), ('measured, 95% CI', S1, 'dot')])
    return ''.join(o) + '</svg>'


def main(argv):
    dirs = [a for a in argv if not a.startswith('--')]
    out = 'figures'
    for i, a in enumerate(argv):
        if a == '--out' and i + 1 < len(argv):
            out = argv[i + 1]
    os.makedirs(out, exist_ok=True)
    data = load(dirs or ['results/battery'])
    if not data:
        print('no summary.json found in: ' + ', '.join(dirs)); return 1

    brows, prows = [], []
    for cell, spec in CELLS.items():
        reps = data.get(cell, [])
        if not reps:
            continue
        base, _ = ci([r[0] for r in reps])
        d, dhw = ci([r[1] - r[0] for r in reps])
        spans = st.mean([r[2] for r in reps if r[2]]) if any(r[2] for r in reps) else spec['spans']
        ov, ovhw = 100 * d / base, 100 * dhw / base
        if spec['lo'] is not None and EDGES[cell][0] <= base < EDGES[cell][1]:
            brows.append((cell, spec['role'].split('"')[1], ov, ovhw, 100 * spans * C_SPAN_MS / base,
                          spec['lo'], spec['hi']))
        if spans >= 8:
            prows.append((cell, spans, d / spans * 1000, dhw / spans * 1000))

    wrote = []
    if brows:
        p = os.path.join(out, 'bucket-verdict.svg')
        open(p, 'w').write(bucket_verdict(brows)); wrote.append(p)
    if prows:
        p = os.path.join(out, 'per-span-cost.svg')
        open(p, 'w').write(per_span(prows)); wrote.append(p)
    print('wrote ' + ', '.join(wrote) if wrote else 'nothing to draw')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
