#!/usr/bin/env python3
"""The report's figure: overhead against the CPU a request already costs.

One curve, the constant divided by the x axis, and the four measured
deployments with their 95% intervals. Drawn as plain SVG so it embeds in the
HTML and survives the print to PDF with no scripts and no fonts to load.

The geometry is computed from the same summary the tables come from, so the
figure cannot drift away from the numbers beside it.
"""

SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
MUTED = '#8a8985'
GRID = '#e6e6e3'
SERIES = '#2a78d6'

W, H = 620, 290
L, R, T, B = 58, 16, 18, 42          # margins


def svg(rows, const_us, x_max=66.0, y_lo=-4.0, y_hi=14.0):
    """rows: [(label, cpu_ms, pct, ci_lo_pct, ci_hi_pct)]"""
    pw, ph = W - L - R, H - T - B

    def px(v):
        return L + v / x_max * pw

    def py(v):
        return T + (y_hi - v) / (y_hi - y_lo) * ph

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
         f'role="img" aria-label="Overhead against CPU per request">',
         f'<rect width="{W}" height="{H}" fill="{SURFACE}"/>']

    # horizontal grid + y labels
    v = y_lo
    while v <= y_hi + 0.01:
        if abs(v % 2) < 0.01:
            y = py(v)
            zero = abs(v) < 0.01
            o.append(f'<line x1="{L}" y1="{y:.1f}" x2="{L + pw}" y2="{y:.1f}" '
                     f'stroke="{INK_2 if zero else GRID}" stroke-width="{1 if zero else 1}" '
                     f'{"" if zero else ""}/>')
            o.append(f'<text x="{L - 9}" y="{y + 4:.1f}" text-anchor="end" font-size="11" '
                     f'fill="{INK_2}">{v:.0f}%</text>')
        v += 2

    # x ticks
    for xv in range(0, int(x_max) + 1, 10):
        x = px(xv)
        o.append(f'<line x1="{x:.1f}" y1="{T + ph}" x2="{x:.1f}" y2="{T + ph + 5}" '
                 f'stroke="{MUTED}" stroke-width="1"/>')
        o.append(f'<text x="{x:.1f}" y="{T + ph + 19}" text-anchor="middle" font-size="11" '
                 f'fill="{INK_2}">{xv}</text>')
    o.append(f'<text x="{L + pw / 2:.0f}" y="{H - 8}" text-anchor="middle" font-size="11.5" '
             f'fill="{INK}">CPU the request already costs (ms)</text>')
    o.append(f'<text transform="translate(15,{T + ph / 2:.0f}) rotate(-90)" text-anchor="middle" '
             f'font-size="11.5" fill="{INK}">Overhead (% of the request\'s own CPU)</text>')

    # the model curve: constant / x
    pts = []
    x = 1.6
    while x <= x_max:
        y = const_us / 1000.0 / x * 100.0
        if y <= y_hi:
            pts.append(f'{px(x):.1f},{py(y):.1f}')
        x += 0.25
    o.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{INK_2}" stroke-width="2" '
             f'stroke-dasharray="6 4" stroke-linecap="round"/>')

    # measured deployments. A deployment whose cost did not clear its own noise
    # gets its range and no centre dot: the point estimate there is not a
    # measurement of anything, and drawing it invites the reader to believe it.
    for row in rows:
        label, cpu, pct, lo, hi = row[:5]
        resolved = row[5] if len(row) > 5 else True
        x, y = px(cpu), py(pct)
        ylo, yhi = py(max(lo, y_lo)), py(min(hi, y_hi))
        o.append(f'<line x1="{x:.1f}" y1="{ylo:.1f}" x2="{x:.1f}" y2="{yhi:.1f}" '
                 f'stroke="{SERIES}" stroke-width="2" stroke-linecap="round" opacity="0.5"/>')
        for yy in (ylo, yhi):
            o.append(f'<line x1="{x - 4:.1f}" y1="{yy:.1f}" x2="{x + 4:.1f}" y2="{yy:.1f}" '
                     f'stroke="{SERIES}" stroke-width="2" stroke-linecap="round" opacity="0.5"/>')
        if resolved:
            o.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{SERIES}" stroke="{SURFACE}" '
                     f'stroke-width="2"/>')
        o.append(f'<text x="{x:.1f}" y="{yhi - 9:.1f}" text-anchor="middle" font-size="11" '
                 f'font-weight="600" fill="{INK}">{label}</text>')

    # legend, placed in the empty upper right
    lx, ly = L + pw - 232, T + 14
    o.append(f'<line x1="{lx}" y1="{ly - 4}" x2="{lx + 22}" y2="{ly - 4}" stroke="{INK_2}" '
             f'stroke-width="2" stroke-dasharray="6 4"/>')
    o.append(f'<text x="{lx + 29}" y="{ly}" font-size="11" fill="{INK_2}">'
             f'one fixed cost ÷ CPU per request</text>')
    o.append(f'<circle cx="{lx + 11}" cy="{ly + 15}" r="5" fill="{SERIES}"/>')
    o.append(f'<text x="{lx + 29}" y="{ly + 19}" font-size="11" fill="{INK_2}">'
             f'measured; bar alone = below resolution</text>')

    o.append('</svg>')
    # One line: the report embeds this inside a single Markdown line, and a
    # multi-line block would be parsed as prose.
    return ''.join(o)
