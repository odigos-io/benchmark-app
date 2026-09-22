#!/usr/bin/env python3
"""Spans the sink received for one cell during the measured window.

Two scrapes of the nop collector, each a timestamp followed by the collector's
own metrics (:8888) and the spanmetrics connector's output (:8889), delimited
by "@@ 8888" / "@@ 8889" lines. The receiver counter is the total delivered by
the gateway across every cell; the spanmetrics `calls` counter carries
service.name, which is how one shared sink yields a per-cell spans/req.

    python3 sink_delta.py <before> <after> --service bucket-app-m --requests N [--expect-spans 7]
"""

import json
import re
import sys

LINE = re.compile(r'^([A-Za-z_:][A-Za-z0-9_:]*)(\{([^}]*)\})?\s+([-+0-9.eEnaNif]+)')
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse(path):
    out = {'ts_ns': None, 'metrics': []}
    section = None
    for ln in open(path):
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith('TS='):
            out['ts_ns'] = int(ln[3:])
            continue
        if ln.startswith('@@'):
            section = ln.split()[1] if len(ln.split()) > 1 else None
            continue
        if ln.startswith('#'):
            continue
        m = LINE.match(ln)
        if not m:
            continue
        name, labels, value = m.group(1), m.group(3) or '', m.group(4)
        try:
            v = float(value)
        except ValueError:
            continue
        out['metrics'].append((section, name, dict(LABEL.findall(labels)), v))
    return out


def total(snap, base, where=None):
    """Sum of a counter family across labels; accepts the _total suffix."""
    s = 0.0
    seen = False
    for section, name, labels, v in snap['metrics']:
        if name not in (base, base + '_total'):
            continue
        if where and any(labels.get(k) != val for k, val in where.items()):
            continue
        s += v
        seen = True
    return s if seen else None


def calls_by(snap, service, key):
    out = {}
    for section, name, labels, v in snap['metrics']:
        if name not in ('calls', 'calls_total', 'traces_span_metrics_calls', 'traces_span_metrics_calls_total'):
            continue
        if labels.get('service_name') != service:
            continue
        out[labels.get(key, '')] = out.get(labels.get(key, ''), 0.0) + v
    return out


def delta(a, b):
    if a is None or b is None:
        return None
    return b - a


def main():
    args = sys.argv[1:]
    pos, opts = [], {}
    i = 0
    while i < len(args):
        if args[i].startswith('--'):
            opts[args[i][2:]] = args[i + 1]
            i += 2
        else:
            pos.append(args[i])
            i += 1
    before, after = parse(pos[0]), parse(pos[1])
    service = opts.get('service')
    requests = int(opts.get('requests', '0') or 0)
    expect = float(opts['expect-spans']) if opts.get('expect-spans') else None

    window_s = ((after['ts_ns'] - before['ts_ns']) / 1e9
                if before['ts_ns'] and after['ts_ns'] else None)

    out = {
        'service': service,
        'window_s': round(window_s, 3) if window_s else None,
        'requests': requests,
        'receiver_accepted_delta': delta(total(before, 'otelcol_receiver_accepted_spans'),
                                         total(after, 'otelcol_receiver_accepted_spans')),
        'receiver_refused_delta': delta(total(before, 'otelcol_receiver_refused_spans'),
                                        total(after, 'otelcol_receiver_refused_spans')),
        'exporter_failed_delta': delta(total(before, 'otelcol_exporter_send_failed_spans'),
                                       total(after, 'otelcol_exporter_send_failed_spans')),
    }

    if service:
        kb, ka = calls_by(before, service, 'span_kind'), calls_by(after, service, 'span_kind')
        by_kind = {k: ka[k] - kb.get(k, 0.0) for k in ka}
        nb, na = calls_by(before, service, 'span_name'), calls_by(after, service, 'span_name')
        by_name = {k: na[k] - nb.get(k, 0.0) for k in na}
        spans = sum(by_kind.values())
        out['service_spans_delta'] = spans
        out['by_kind'] = {k.replace('SPAN_KIND_', ''): v for k, v in sorted(by_kind.items())}
        out['by_name'] = dict(sorted(by_name.items(), key=lambda kv: -kv[1])[:16])
        out['spans_per_req'] = round(spans / requests, 4) if requests else None
        if expect and requests:
            out['expect_spans'] = expect
            out['keep_rate'] = round(spans / requests / expect, 4)
    json.dump(out, sys.stdout, indent=2)
    print()


if __name__ == '__main__':
    main()
