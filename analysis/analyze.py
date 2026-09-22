#!/usr/bin/env python3
"""Overhead per bucket, from one or more bracketed runs.

Two views of the same accepted arms, never of rejected ones:

Pooled (headline). Every arm is a fresh JVM, and baseline CPU/req differs 2-6%
between JVM instances of the same cell, so a single off/on/off triplet cannot
resolve a few-percent effect. Across all runs given, every accepted arm of a
cell is pooled by kind (off, 100, 25): n, mean, sd, min, max per metric;
delta = mean(on) - mean(off) with a 95% confidence interval from the Welch
two-sample standard error and Student t for the Welch degrees of freedom. A
delta whose interval contains zero is "not resolved".

Bracketed (secondary). Arms alternate off / instrumented / off; each
instrumented arm is compared with the mean of its two accepted neighbours,
and their relative gap is the drift band of that rep. A cell-rep with a band
above BAND_LIMIT_PCT or a missing neighbour is listed for rerun.

Two CPU figures per arm: the container cgroup (everything the JVM did) and the
app's own request-thread CPU (ThreadMXBean, excludes JIT and GC threads; the
agent's uprobe/JNI work lands here too). The second is derived from the raw
scrapes when an older jvm.json lacks it.

    python3 analyze.py <run-dir> [<run-dir> ...] [--json summary.json]

Run directories that are incomplete (a killed arm without k6.json) or fully
rejected are reported and skipped, never fatal.
"""

import json
import math
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'bin', 'lib'))
import jvm_delta  # noqa: E402

BAND_LIMIT_PCT = float(os.environ.get('BAND_LIMIT_PCT', '3.0'))
# Extrapolate the baseline to the instrumented window's time instead of
# averaging the uninstrumented windows. Off by default: see the header.
BASELINE_FIT = os.environ.get('BASELINE_FIT', '') not in ('', '0', 'false')

# Bucket label from the MEASURED baseline CPU ms/req, never from the knob.
BUCKETS = (('S', 0.0, 8.0), ('M', 8.0, 20.0), ('L', 20.0, 45.0), ('XL', 45.0, float('inf')))

# (key, label, higher_is_worse)
METRICS = (
    ('cpu_ms_per_req', 'CPU ms/req', True),
    ('thread_cpu_ms_per_req', 'thread CPU ms/req', True),
    ('tps', 'throughput', False),
    ('avg_ms', 'avg latency', True),
    ('med_ms', 'p50 latency', True),
    ('p95_ms', 'p95 latency', True),
    ('p99_ms', 'p99 latency', True),
)
POOLED_KEYS = ('cpu_ms_per_req', 'thread_cpu_ms_per_req', 'med_ms', 'p95_ms', 'p99_ms')

# Columns carried on the instrumented arm as absolute values.
ABSOLUTE = ('spans_per_req', 'keep_rate', 'heap_used_mb_max', 'gc_pause_pct', 'alloc_mb_per_s',
            'alloc_mb_per_req', 'compile_ms_per_min', 'mem_peak_mb', 'restarts', 'odiglet_cores',
            'odiglet_us_per_span', 'node_collector_cores', 'root_cores_avg', 'cpu_cores_avg', 'errors_pct')

# Two-sided 95% Student t, df 1..30; 1.96 beyond.
T975 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
        2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
        2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)


def t_crit(df):
    df = max(1, int(math.floor(df)))
    return T975[df - 1] if df <= len(T975) else 1.96


def jload(path):
    if os.path.exists(path) and os.path.getsize(path):
        try:
            return json.load(open(path))
        except Exception:
            return None
    return None


def bucket_of(cpu_ms):
    if cpu_ms is None:
        return None
    for name, lo, hi in BUCKETS:
        if lo <= cpu_ms < hi:
            return name
    return None


def snap_ts(path):
    if not os.path.exists(path):
        return None
    for ln in open(path):
        if ln.startswith('TS='):
            try:
                return int(ln[3:].strip())
            except ValueError:
                return None
    return None


def load_arm(run, slot, cell):
    d = os.path.join(run, slot)
    p = lambda s: os.path.join(d, f'{cell}.{s}.json')
    k = jload(p('k6'))
    if not k:
        return None
    o = k['overall']
    row = {'slot': slot, 'arm': slot.split('-', 1)[1], 'accepted': True, 'reasons': []}
    g = jload(p('gate'))
    if g is not None:
        row['accepted'] = g.get('ok', False)
        row['reasons'] = g.get('reasons', [])
    row.update({m: o.get(m) for m in ('tps', 'avg_ms', 'med_ms', 'p95_ms', 'p99_ms', 'requests')})
    row['rate'] = float(k.get('rate') or 0) or None
    row['errors_pct'] = (o.get('failed_rate') or 0) * 100
    row['t0'] = snap_ts(os.path.join(d, f'{cell}.cg.start'))
    row['check_fail'] = o.get('check_fail')
    c = jload(p('cpu'))
    if c:
        row['t0'] = c.get('t0_ns')
        row['cpu_ms_per_req'] = c.get('cpu_ms_per_req')
        row['cpu_cores_avg'] = c.get('cpu_cores_avg')
        row['window_s'] = c.get('window_s')
        row['throttled'] = c.get('nr_throttled_delta', 0)
        row['mem_peak_mb'] = c.get('mem_peak_mb')
        row['cpu_max'] = c.get('cpu_max')
        row['cpuset'] = c.get('cpuset')
    h1, h2 = jload(p('cpu.h1')), jload(p('cpu.h2'))
    if h1 and h2 and h1.get('cpu_ms_per_req') and h2.get('cpu_ms_per_req'):
        a, b = h1['cpu_ms_per_req'], h2['cpu_ms_per_req']
        row['half_gap_pct'] = abs(a - b) / ((a + b) / 2) * 100
    s = jload(p('sink'))
    if s:
        row['spans_per_req'] = s.get('spans_per_req')
        row['keep_rate'] = s.get('keep_rate')
        row['spans'] = s.get('service_spans_delta')
        row['by_kind'] = s.get('by_kind')
    j = jload(p('jvm')) or {}
    if 'thread_cpu_ms_per_req' not in j:
        # Older jvm.json: derive the request-thread figures from the raw scrapes.
        st, en = os.path.join(d, f'{cell}.jvm.start'), os.path.join(d, f'{cell}.jvm.end')
        if os.path.exists(st) and os.path.exists(en) and os.path.getsize(st) and os.path.getsize(en):
            try:
                fresh = jvm_delta.compute(st, en)
                for key in ('thread_cpu_ms_per_req', 'alloc_mb_per_req', 'thread_cpu_s'):
                    j.setdefault(key, fresh.get(key))
            except Exception:
                pass
    for key in ('heap_used_mb_max', 'gc_pause_pct', 'alloc_mb_per_s', 'alloc_mb_per_req',
                'compile_ms_per_min', 'thread_cpu_ms_per_req'):
        if j.get(key) is not None:
            row[key] = j[key]
    n = jload(p('probe'))
    if n:
        for key in ('odiglet_cores', 'odiglet_us_per_span', 'node_collector_cores', 'root_cores_avg'):
            row[key] = n.get(key)
    m = jload(p('meta'))
    if m:
        row['meta'] = m
        rs, re_ = m.get('restarts_start'), m.get('restarts_end')
        row['restarts'] = (re_ - rs) if isinstance(rs, int) and isinstance(re_, int) else None
    return row


def neighbours(arms, i):
    """Nearest accepted uninstrumented arm on each side of index i."""
    before = next((s for s in reversed(arms[:i]) if s['arm'] == 'off' and s['accepted']), None)
    after = next((s for s in arms[i + 1:] if s['arm'] == 'off' and s['accepted']), None)
    return before, after


def bracket(arms, i):
    a = arms[i]
    b1, b2 = neighbours(arms, i)
    base = [x for x in (b1, b2) if x]
    entry = {'arm': a['arm'], 'slot': a['slot'], 'baselines': len(base),
             'baseline_slots': [x['slot'] for x in base], 'valid': True, 'invalid_reasons': []}
    if not b1 or not b2:
        entry['valid'] = False
        entry['invalid_reasons'].append('missing accepted off neighbour ' + ('before' if not b1 else 'after'))
    if not base:
        return entry
    for key, label, higher_is_worse in METRICS:
        vals = [x.get(key) for x in base if x.get(key)]
        av = a.get(key)
        if not vals or not av:
            continue
        bmean = sum(vals) / len(vals)
        band = (max(vals) - min(vals)) / bmean * 100 if len(vals) > 1 else None
        delta = av - bmean
        pct = delta / bmean * 100
        if not higher_is_worse:
            pct, delta = -pct, -delta
        entry[key] = {'baseline': bmean, 'arm': av, 'delta': delta, 'delta_pct': pct,
                      'band_pct': band, 'band_ms': (max(vals) - min(vals)) if len(vals) > 1 else None,
                      'below_resolution': band is not None and abs(pct) < band}
    cpu = entry.get('cpu_ms_per_req')
    if cpu:
        entry['cpu_ms_per_req']['delta_ms'] = cpu['delta']
        if cpu['band_pct'] is not None and cpu['band_pct'] > BAND_LIMIT_PCT:
            entry['valid'] = False
            entry['invalid_reasons'].append(f'band {cpu["band_pct"]:.2f}% > {BAND_LIMIT_PCT}%')
    else:
        entry['valid'] = False
        entry['invalid_reasons'].append('no CPU/req on arm or baselines')
    for key in ABSOLUTE:
        if a.get(key) is not None:
            entry[key] = a[key]
    roots = [x.get('root_cores_avg') for x in base if x.get('root_cores_avg') is not None]
    if roots and a.get('root_cores_avg') is not None:
        entry['node_total_delta_cores'] = a['root_cores_avg'] - sum(roots) / len(roots)
    entry['bucket'] = bucket_of(cpu['baseline']) if cpu else None
    return entry


def same_pod_rep(arms):
    pods = {(a.get('meta') or {}).get('pod') for a in arms if a.get('meta')}
    pods.discard(None)
    return len(pods) == 1


def load_run(run):
    """One rep. Returns ({cell: rep}, notes); notes explain anything skipped."""
    notes = []
    if not os.path.isdir(run):
        return {}, [f'{run}: not a directory']
    slots = sorted(d for d in os.listdir(run) if re.match(r'^\d\d-', d))
    if not slots:
        return {}, [f'{run}: no NN-<arm> slots']
    for s in slots:
        if not any(f.endswith('.k6.json') for f in os.listdir(os.path.join(run, s))):
            notes.append(f'{os.path.basename(run.rstrip("/"))}/{s}: no k6 summary (arm killed or unfinished) - skipped')
    cells = sorted({f.split('.')[0] for s in slots for f in os.listdir(os.path.join(run, s))
                    if f.endswith('.k6.json')})
    out = {}
    for cell in cells:
        arms = [a for a in (load_arm(run, s, cell) for s in slots) if a]
        if not arms:
            continue
        if not any(a['accepted'] for a in arms):
            notes.append(f'{os.path.basename(run.rstrip("/"))} {cell}: every arm rejected - contributes nothing')
        meta = next((a['meta'] for a in arms if a.get('meta')), {})
        one_jvm = same_pod_rep(arms)
        results = [bracket(arms, i) for i, a in enumerate(arms)
                   if a['arm'] != 'off' and a['accepted']]
        if one_jvm:
            # Every arm ran on one JVM; the bracket's neighbour rule does not
            # apply and the paired view carries this rep instead.
            for r in results:
                reasons = [x for x in r.get('invalid_reasons', []) if 'neighbour' not in x]
                if not reasons and not r.get('valid'):
                    r['valid'] = True
                r['invalid_reasons'] = reasons
                r['same_jvm'] = True
        for a in arms:
            if a['arm'] != 'off' and not a['accepted']:
                results.append({'arm': a['arm'], 'slot': a['slot'], 'valid': False,
                                'invalid_reasons': ['arm rejected: ' + '; '.join(a['reasons'])[:160]]})
        nodes = sorted({a['meta'].get('node') for a in arms if a.get('meta') and a['meta'].get('node')})
        qos = sorted({a['meta'].get('qos') for a in arms if a.get('meta') and a['meta'].get('qos')})
        out[cell] = {'run': run, 'same_jvm': one_jvm,
                     'preset': meta.get('preset') or meta.get('scenario'),
                     'expect_spans': meta.get('expect_spans'), 'rate': meta.get('arrival_rate'),
                     'nodes': nodes, 'qos': qos, 'arms': arms, 'results': results}
    return out, notes


def stat(values):
    v = [x for x in values if x is not None]
    if not v:
        return None
    return {'median': statistics.median(v), 'min': min(v), 'max': max(v), 'n': len(v)}


def describe(values):
    v = [x for x in values if x is not None]
    if not v:
        return None
    return {'n': len(v), 'mean': statistics.fmean(v), 'sd': statistics.stdev(v) if len(v) > 1 else None,
            'min': min(v), 'max': max(v), 'values': v}


def welch(on, off):
    """mean(on) - mean(off) with a 95% CI; None fields when either side has n < 2."""
    if not on or not off:
        return None
    d = on['mean'] - off['mean']
    out = {'delta': d, 'delta_pct': d / off['mean'] * 100 if off['mean'] else None,
           'n_on': on['n'], 'n_off': off['n'], 'se': None, 'df': None, 'ci_lo': None, 'ci_hi': None,
           'ci_pct_lo': None, 'ci_pct_hi': None, 'resolved': None}
    if on['n'] < 2 or off['n'] < 2:
        return out
    v1, v2 = on['sd'] ** 2 / on['n'], off['sd'] ** 2 / off['n']
    se = math.sqrt(v1 + v2)
    if se == 0:
        df = on['n'] + off['n'] - 2
    else:
        df = (v1 + v2) ** 2 / (v1 ** 2 / (on['n'] - 1) + v2 ** 2 / (off['n'] - 1))
    hw = t_crit(df) * se
    out.update({'se': se, 'df': df, 'half_width': hw, 'ci_lo': d - hw, 'ci_hi': d + hw,
                'ci_pct_lo': (d - hw) / off['mean'] * 100 if off['mean'] else None,
                'ci_pct_hi': (d + hw) / off['mean'] * 100 if off['mean'] else None,
                'resolved': not (d - hw <= 0 <= d + hw)})
    return out


def pooled(reps):
    """Every accepted arm of the cell across all reps, by arm kind."""
    kinds = {}
    for rep in reps:
        for a in rep['arms']:
            if a['accepted']:
                kinds.setdefault(a['arm'], []).append(dict(a, run=rep['run']))
    out = {'arms': {}, 'deltas': {}}
    for kind, rows in kinds.items():
        out['arms'][kind] = {'n': len(rows),
                             'slots': [f'{os.path.basename(r["run"].rstrip("/"))}/{r["slot"]}' for r in rows]}
        for key in POOLED_KEYS + ABSOLUTE:
            out['arms'][kind][key] = describe([r.get(key) for r in rows])
    off = out['arms'].get('off')
    for kind in sorted((k for k in kinds if k != 'off'), key=lambda k: -float(k)):
        on = out['arms'][kind]
        out['deltas'][kind] = {key: welch(on.get(key), off.get(key) if off else None) for key in POOLED_KEYS}
    base = (off or {}).get('cpu_ms_per_req')
    out['bucket'] = bucket_of(base['mean']) if base else None
    return out


def aggregate(reps):
    """Bracket view per instrumented arm: median and range across valid cell-reps."""
    out = {}
    arms = sorted({r['arm'] for rep in reps for r in rep['results']}, key=lambda a: -float(a))
    for arm in arms:
        rows = [r for rep in reps for r in rep['results'] if r['arm'] == arm]
        valid = [r for r in rows if r.get('valid')]
        agg = {'n_reps': len(rows), 'n_valid': len(valid),
               'invalid': [{'run': rep['run'], 'slot': r['slot'], 'reasons': r['invalid_reasons']}
                           for rep in reps for r in rep['results'] if r['arm'] == arm and not r.get('valid')]}
        for key, label, _ in METRICS:
            agg[key] = {
                'baseline': stat([r[key]['baseline'] for r in valid if key in r]),
                'arm': stat([r[key]['arm'] for r in valid if key in r]),
                'delta': stat([r[key]['delta'] for r in valid if key in r]),
                'delta_pct': stat([r[key]['delta_pct'] for r in valid if key in r]),
                'band_pct': stat([r[key]['band_pct'] for r in valid if key in r and r[key]['band_pct'] is not None]),
                'below_resolution_any': any(r[key]['below_resolution'] for r in valid if key in r),
                'below_resolution_all': bool(valid) and all(r[key]['below_resolution'] for r in valid if key in r),
            }
        for key in ABSOLUTE + ('node_total_delta_cores',):
            agg[key] = stat([r.get(key) for r in valid])
        agg['bucket'] = bucket_of(agg['cpu_ms_per_req']['baseline']['median']) \
            if agg['cpu_ms_per_req']['baseline'] else None
        out[arm] = agg
    return out


def paired(reps):
    """Same-JVM view: an instrumented arm is compared only with the off arms that
    ran on the SAME pod, so the 2-15% difference in compiled-code quality between
    JVM instances never enters the delta. Across reps the paired differences are
    averaged and carry a Student t interval on df = n-1.

    Produced by RESTART=once runs, where Odigos attaches to the running JVM and
    every arm of a rep shares one pod. Reps whose arms sit on different pods
    contribute nothing here and are listed under `skipped`.
    """
    per_arm, skipped = {}, []
    for rep in reps:
        by_pod = {}
        for a in rep['arms']:
            pod = (a.get('meta') or {}).get('pod')
            if a['accepted'] and pod:
                by_pod.setdefault(pod, []).append(a)
        run_name = os.path.basename(rep['run'].rstrip('/'))
        for pod, arms in by_pod.items():
            arms = sorted(arms, key=lambda a: a['slot'])
            first_on = next((i for i, a in enumerate(arms) if a['arm'] != 'off'), len(arms))
            offs = [a for a in arms[:first_on] if a['arm'] == 'off']
            trailing_offs = [a for a in arms[first_on:] if a['arm'] == 'off']
            ons = [a for a in arms if a['arm'] != 'off']
            if not offs or not ons:
                if ons:
                    skipped.append(f'{run_name} pod ...{pod[-12:]}: instrumented arm with no off arm on the same JVM')
                continue
            by_kind = {}
            for on in ons:
                by_kind.setdefault(on['arm'], []).append(on)
            for kind, group in by_kind.items():
                # The repetition is the independent unit: windows of the same
                # kind on one JVM share a baseline and are averaged first.
                on = {'arm': kind, 'slot': '+'.join(a['slot'] for a in group)}
                for key in POOLED_KEYS + ABSOLUTE + ('t0',):
                    vals = [a.get(key) for a in group if a.get(key) is not None]
                    on[key] = statistics.fmean(vals) if vals else None
                row = {'run': rep['run'], 'run_name': run_name, 'pod': pod, 'slot': on['slot'],
                       'n_windows': len(group), 'n_off': len(offs),
                       'off_slots': [o['slot'] for o in offs]}
                for key in POOLED_KEYS:
                    pairs = [(o.get('t0'), o.get(key)) for o in offs if o.get(key) is not None]
                    ovals = [v for _, v in pairs]
                    v = on.get(key)
                    if not ovals or v is None:
                        continue
                    base = statistics.fmean(ovals)
                    band = (max(ovals) - min(ovals)) / base * 100 if base and len(ovals) > 1 else None
                    fitted = None
                    ts = [t for t, _ in pairs if t is not None]
                    if len(ts) == len(pairs) and len(ts) > 1 and on.get('t0') and len(set(ts)) > 1:
                        # seconds, relative, so the fit is numerically tame
                        t0 = min(ts)
                        xs = [(t - t0) / 1e9 for t in ts]
                        ys = ovals
                        mx, my = statistics.fmean(xs), statistics.fmean(ys)
                        den = sum((x - mx) ** 2 for x in xs)
                        if den:
                            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
                            fitted = my + slope * ((on['t0'] - t0) / 1e9 - mx)
                    eff = fitted if (BASELINE_FIT and fitted is not None) else base
                    row[key] = {'baseline': base, 'baseline_fitted': fitted, 'baseline_used': eff,
                                'drift_corrected': BASELINE_FIT and fitted is not None,
                                'arm': v, 'delta': v - eff, 'delta_raw': v - base,
                                'delta_pct': (v - eff) / eff * 100 if eff else None,
                                'band_pct': band}
                for key in ABSOLUTE:
                    if on.get(key) is not None:
                        row[key] = on.get(key)
                per_arm.setdefault(on['arm'], []).append(row)
            for t in trailing_offs:
                row = {'run': rep['run'], 'run_name': run_name, 'pod': pod, 'slot': t['slot'],
                       'n_off': len(offs), 'off_slots': [o['slot'] for o in offs]}
                for key in POOLED_KEYS:
                    pairs = [(o.get('t0'), o.get(key)) for o in offs if o.get(key) is not None]
                    ovals = [v for _, v in pairs]
                    v = t.get(key)
                    if not ovals or v is None:
                        continue
                    base = statistics.fmean(ovals)
                    fitted = None
                    ts = [x for x, _ in pairs if x is not None]
                    if len(ts) == len(pairs) and len(ts) > 1 and t.get('t0') and len(set(ts)) > 1:
                        t0 = min(ts)
                        xs = [(x - t0) / 1e9 for x in ts]
                        mx, my = statistics.fmean(xs), statistics.fmean(ovals)
                        den = sum((x - mx) ** 2 for x in xs)
                        if den:
                            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ovals)) / den
                            fitted = my + slope * ((t['t0'] - t0) / 1e9 - mx)
                    eff = fitted if (BASELINE_FIT and fitted is not None) else base
                    row[key] = {'baseline': base, 'baseline_fitted': fitted, 'baseline_used': eff,
                                'drift_corrected': BASELINE_FIT and fitted is not None, 'arm': v,
                                'delta': v - eff, 'delta_raw': v - base,
                                'delta_pct': (v - eff) / eff * 100 if eff else None,
                                'band_pct': None}
                per_arm.setdefault('detached', []).append(row)

    out = {'arms': {}, 'skipped': skipped,
           'n_pods': len({r['pod'] for rows in per_arm.values() for r in rows})}
    for arm, rows in per_arm.items():
        entry = {'n': len(rows),
                 'pairs': [f'{r["run_name"]}/{r["slot"]}@...{r["pod"][-12:]}' for r in rows]}
        for key in POOLED_KEYS:
            deltas = [r[key]['delta'] for r in rows if key in r]
            bases = [r[key].get('baseline_used') or r[key]['baseline'] for r in rows if key in r]
            if not deltas:
                continue
            n = len(deltas)
            mean = statistics.fmean(deltas)
            sd = statistics.stdev(deltas) if n > 1 else None
            corrected = sum(1 for r in rows if key in r and r[key].get('drift_corrected'))
            raws = [r[key]['delta_raw'] for r in rows if key in r and r[key].get('delta_raw') is not None]
            d = {'n': n, 'delta': mean, 'sd': sd, 'min': min(deltas), 'max': max(deltas),
                 'n_drift_corrected': corrected,
                 'delta_uncorrected': statistics.fmean(raws) if raws else None,
                 'baseline': statistics.fmean(bases) if bases else None,
                 'se': None, 'half_width': None, 'ci_lo': None, 'ci_hi': None,
                 'ci_pct_lo': None, 'ci_pct_hi': None, 'delta_pct': None, 'resolved': None}
            if n > 1 and sd is not None:
                se = sd / math.sqrt(n)
                hw = t_crit(n - 1) * se
                d.update({'se': se, 'half_width': hw, 'ci_lo': mean - hw, 'ci_hi': mean + hw,
                          'resolved': not (mean - hw <= 0 <= mean + hw)})
            if d['baseline']:
                d['delta_pct'] = mean / d['baseline'] * 100
                if d['ci_lo'] is not None:
                    d['ci_pct_lo'] = d['ci_lo'] / d['baseline'] * 100
                    d['ci_pct_hi'] = d['ci_hi'] / d['baseline'] * 100
            entry[key] = d
        for key in ABSOLUTE:
            entry[key] = describe([r.get(key) for r in rows])
        entry['band_pct'] = describe([r['cpu_ms_per_req'].get('band_pct') for r in rows
                                      if 'cpu_ms_per_req' in r
                                      and r['cpu_ms_per_req'].get('band_pct') is not None])
        out['arms'][arm] = entry
    out['residual_after_detach'] = out['arms'].pop('detached', None)
    base = (out['arms'].get('100') or out['arms'].get('25') or {}).get('cpu_ms_per_req')
    out['bucket'] = bucket_of(base['baseline']) if base and base.get('baseline') else None
    return out


def fit_model_paired(cells, arm):
    pts = []
    for cell, c in cells.items():
        e = (c.get('paired') or {}).get('arms', {}).get(arm) or {}
        d = e.get('cpu_ms_per_req')
        spans = c.get('expect_spans') or (e.get('spans_per_req') or {}).get('mean')
        if not d or not spans:
            continue
        pts.append({'cell': cell, 'spans_per_req': spans, 'delta_ms': d['delta'],
                    'se_ms': d.get('se'), 'hw_ms': d.get('half_width'),
                    'resolved': d.get('resolved'), 'baseline_ms': d.get('baseline')})
    if not pts:
        return None
    pts.sort(key=lambda p: p['cell'])
    c_span, hw = fit_points(pts)
    return {'c_span_us': c_span * 1000 if c_span is not None else None,
            'c_span_hw_us': hw * 1000 if hw is not None else None,
            'points': pts, 'reference_point': REFERENCE}


def pct_ci_str(d):
    if not d or d.get('delta_pct') is None:
        return '-'
    s = f'{d["delta_pct"]:+.2f}%'
    if d.get('ci_pct_lo') is None:
        return s + ' (n<2)'
    return s + f' [{d["ci_pct_lo"]:+.2f}, {d["ci_pct_hi"]:+.2f}]'


def print_paired(cells):
    rows = [(cell, c) for cell, c in cells.items() if (c.get('paired') or {}).get('arms')]
    if not rows:
        return
    print('\n=== paired: instrumented arm vs the same JVM\'s own off arms (headline) ===')
    print('  cell       arm   n bucket    base ms/req             delta us [95% CI]'
          '               delta % [CI]     thr delta us  band %  resolved')
    for cell, c in sorted(rows):
        for arm in sorted(c['paired']['arms'], key=lambda a: -float(a) if a.replace('.', '').isdigit() else 1):
            e = c['paired']['arms'][arm]
            d = e.get('cpu_ms_per_req')
            if not d:
                continue
            t = e.get('thread_cpu_ms_per_req') or {}
            band = e.get('band_pct') or {}
            bandtxt = f'{band["mean"]:.2f}' if band.get('mean') is not None else '-'
            res = 'yes' if d.get('resolved') else ('NO' if d.get('resolved') is False else '-')
            print(f'  {cell:<9} {arm:>4} {d["n"]:>3} {str(c["paired"].get("bucket")):>6} '
                  f'{d["baseline"]:>13.3f} {ci_str(d):>29} {pct_ci_str(d):>26} '
                  f'{ci_str(t):>16} {bandtxt:>7}  {res}')
    res = [(cell, c['paired']['residual_after_detach']) for cell, c in rows
           if c['paired'].get('residual_after_detach')]
    if res:
        print('\n  after the Source is deleted (woven, no longer traced) - not a baseline:')
        for cell, e in sorted(res):
            d = e.get('cpu_ms_per_req')
            if d:
                print(f'  {cell:<9} {"det":>4} {d["n"]:>3} {"":>6} {d["baseline"]:>13.3f} '
                      f'{ci_str(d):>29} {pct_ci_str(d):>26}')
    seen = []
    for cell, c in rows:
        for s in c['paired'].get('skipped', []):
            if s not in seen:
                seen.append(s)
    for s in seen:
        print('  note: ' + s)


def fit_points(pts):
    """delta_ms = c_span * spans/req through the origin.

    Weighted by 1/se^2 where every point has a standard error: a cell whose
    delta is buried in noise then informs the constant in proportion to what it
    actually resolved, instead of as much as a cell measured to a few percent.
    """
    ses = [p.get('se_ms') for p in pts]
    weighted = all(s for s in ses)
    w = [1.0 / (p['se_ms'] ** 2) for p in pts] if weighted else [1.0] * len(pts)
    sxx = sum(wi * p['spans_per_req'] ** 2 for wi, p in zip(w, pts))
    if not sxx:
        return None, None
    c = sum(wi * p['spans_per_req'] * p['delta_ms'] for wi, p in zip(w, pts)) / sxx
    hw = None
    if weighted:
        hw = 1.96 * math.sqrt(1.0 / sxx)
    elif all(s is not None for s in ses):
        var = sum((p['spans_per_req'] ** 2) * (p['se_ms'] ** 2) for p in pts) / sxx ** 2
        hw = 1.96 * math.sqrt(var)
    for p in pts:
        p['predicted_ms'] = c * p['spans_per_req']
        p['residual_ms'] = p['delta_ms'] - p['predicted_ms']
        p['us_per_span'] = p['delta_ms'] / p['spans_per_req'] * 1000
        if p.get('hw_ms') is not None:
            p['us_per_span_lo'] = (p['delta_ms'] - p['hw_ms']) / p['spans_per_req'] * 1000
            p['us_per_span_hi'] = (p['delta_ms'] + p['hw_ms']) / p['spans_per_req'] * 1000
    return c, hw


def _reference_point():
    """An optional outside measurement to plot the model against.

    Deliberately not committed: it is somebody else's data. Drop a JSON object
    with cpu_ms, spans_per_req and delta_ms at analysis/reference_point.json,
    or point REFERENCE_POINT at one, and the model tables gain a row comparing
    it with the fit. Absent, which is the shipped state, they simply omit it.
    """
    path = os.environ.get('REFERENCE_POINT') or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'reference_point.json')
    try:
        with open(path) as fh:
            r = json.load(fh)
    except (OSError, ValueError):
        return None
    if not all(k in r for k in ('cpu_ms', 'spans_per_req', 'delta_ms')):
        return None
    r.setdefault('label', 'reference measurement')
    r['us_per_span'] = r['delta_ms'] / r['spans_per_req'] * 1000
    return r


REFERENCE = _reference_point()


def fit_model_pooled(cells, arm):
    pts = []
    for cell, c in cells.items():
        d = c['pooled']['deltas'].get(arm, {}).get('cpu_ms_per_req')
        spans = c.get('expect_spans') or ((c['pooled']['arms'].get(arm) or {}).get('spans_per_req') or {}).get('mean')
        if not d or not spans:
            continue
        pts.append({'cell': cell, 'spans_per_req': spans, 'delta_ms': d['delta'], 'se_ms': d.get('se'),
                    'hw_ms': d.get('half_width'), 'resolved': d.get('resolved'),
                    'baseline_ms': c['pooled']['arms']['off']['cpu_ms_per_req']['mean'],
                    'n_on': d['n_on'], 'n_off': d['n_off']})
    if not pts:
        return None
    c, hw = fit_points(pts)
    return {'c_span_us': c * 1000 if c is not None else None,
            'c_span_hw_us': hw * 1000 if hw is not None else None, 'points': pts, 'reference_point': REFERENCE}


def fit_model_bracket(cells, arm):
    pts = []
    for cell, c in cells.items():
        a = c['aggregate'].get(arm)
        if not a or not a['n_valid'] or not a['cpu_ms_per_req']['delta']:
            continue
        spans = c.get('expect_spans') or (a['spans_per_req'] or {}).get('median')
        if not spans:
            continue
        pts.append({'cell': cell, 'spans_per_req': spans,
                    'kept_spans_per_req': (a['spans_per_req'] or {}).get('median'),
                    'delta_ms': a['cpu_ms_per_req']['delta']['median'],
                    'baseline_ms': a['cpu_ms_per_req']['baseline']['median'],
                    'below_resolution': a['cpu_ms_per_req']['below_resolution_all']})
    if not pts:
        return None
    c, _ = fit_points(pts)
    return {'c_span_us': c * 1000 if c is not None else None, 'points': pts, 'reference_point': REFERENCE}


def fmt(v, spec='.3f', suffix=''):
    return '-' if v is None else f'{v:{spec}}{suffix}'


def print_rep(cell, rep):
    print(f'\n=== {cell}  preset={rep["preset"]}  run={os.path.basename(rep["run"].rstrip("/"))} ===')
    for a in rep['arms']:
        if not a['accepted']:
            print(f'  REJECTED {a["slot"]}: {"; ".join(a["reasons"])[:140]}')
    print(f'  {"arm":>8} {"served":>8} {"tps":>8} {"CPU ms/req":>11} {"thr ms/req":>10} {"cores":>6} '
          f'{"p50":>8} {"p95":>8} {"p99":>8} {"err%":>6} {"spans/req":>9} {"heapMB":>7} {"gc%":>5} {"jit/min":>7} {"odiglet":>8}')
    for a in rep['arms']:
        mark = ' ' if a['accepted'] else 'x'
        print(f'{mark} {a["arm"]:>8} {a.get("requests") or 0:>8} {(a.get("tps") or 0):>8.1f} '
              f'{(a.get("cpu_ms_per_req") or 0):>11.4f} {fmt(a.get("thread_cpu_ms_per_req"), ".4f"):>10} '
              f'{(a.get("cpu_cores_avg") or 0):>6.3f} '
              f'{(a.get("med_ms") or 0):>8.2f} {(a.get("p95_ms") or 0):>8.2f} {(a.get("p99_ms") or 0):>8.2f} '
              f'{a.get("errors_pct", 0):>5.2f}% {fmt(a.get("spans_per_req"), ".2f"):>9} '
              f'{fmt(a.get("heap_used_mb_max"), ".0f"):>7} {fmt(a.get("gc_pause_pct"), ".2f"):>5} '
              f'{fmt(a.get("compile_ms_per_min"), ".0f"):>7} {fmt(a.get("odiglet_cores"), ".3f"):>8}')
    for e in rep['results']:
        tag = 'valid' if e.get('valid') else 'INVALID: ' + '; '.join(e['invalid_reasons'])
        print(f'\n  --- {e["arm"]}% ({e["slot"]}, vs {e.get("baselines", 0)} baseline arms) {tag} ---')
        for key, label, _ in METRICS:
            if key not in e:
                continue
            m = e[key]
            band = f'  [band {m["band_pct"]:.2f}%]' if m['band_pct'] is not None else ''
            res = '  (below band)' if m['below_resolution'] else ''
            unit = ' ms' if key != 'tps' else ' /s'
            print(f'    {label:<18} {m["baseline"]:>10.4f} -> {m["arm"]:>10.4f}   '
                  f'{m["delta"]:>+8.4f}{unit} {m["delta_pct"]:>+7.2f}%{band}{res}')
    if len(rep['nodes']) > 1:
        print(f'  WARNING: arms ran on more than one node: {rep["nodes"]}')
    if len(rep['qos']) > 1:
        print(f'  WARNING: QoS class changed between arms: {rep["qos"]}')


def ci_str(d, scale=1000.0, spec='+.0f'):
    if not d:
        return '-'
    s = f'{d["delta"] * scale:{spec}}'
    if d.get('ci_lo') is not None:
        s += f' [{d["ci_lo"] * scale:{spec}}, {d["ci_hi"] * scale:{spec}}]'
    else:
        s += ' [n<2]'
    return s


def print_pooled(cells):
    print('\n=== pooled: every accepted arm, mean and 95% CI (Welch) ===')
    print(f'  {"cell":<8} {"arm":>4} {"n on/off":>8} {"bucket":>6} {"base ms/req (sd)":>18} {"delta us [95% CI]":>26} '
          f'{"delta % [CI]":>26} {"thr delta us [CI]":>26} {"resolved":>8}')
    for cell, c in cells.items():
        p = c['pooled']
        off = p['arms'].get('off')
        if not off:
            print(f'  {cell:<8} no accepted off arm')
            continue
        base = off['cpu_ms_per_req']
        for arm, d in p['deltas'].items():
            cpu, thr = d['cpu_ms_per_req'], d['thread_cpu_ms_per_req']
            if not cpu:
                print(f'  {cell:<8} {arm:>4} no accepted {arm} arm')
                continue
            pct = f'{cpu["delta_pct"]:+.2f}'
            if cpu.get('ci_pct_lo') is not None:
                pct += f' [{cpu["ci_pct_lo"]:+.2f}, {cpu["ci_pct_hi"]:+.2f}]'
            resolved = '-' if cpu['resolved'] is None else ('yes' if cpu['resolved'] else 'NO')
            print(f'  {cell:<8} {arm:>4} {cpu["n_on"]:>3}/{cpu["n_off"]:<4} {p["bucket"] or "-":>6} '
                  f'{base["mean"]:>10.3f} ({fmt(base["sd"], ".3f")}) {ci_str(cpu):>26} {pct:>26} '
                  f'{ci_str(thr):>26} {resolved:>8}')
    print('  resolved = the 95% CI of the cgroup delta excludes 0; NO = below resolution at this n')


def print_aggregate(cells):
    print('\n=== bracketed: median over valid reps, min-max (secondary view) ===')
    print(f'  {"cell":<8} {"arm":>4} {"bucket":>6} {"base ms/req":>12} {"delta us":>18} {"delta %":>18} '
          f'{"band %":>7} {"reps":>5} {"spans/req":>9} {"keep":>5} {"odiglet us/span":>15}')
    for cell, c in cells.items():
        for arm, a in c['aggregate'].items():
            cpu = a['cpu_ms_per_req']
            if not a['n_valid']:
                print(f'  {cell:<8} {arm:>4}   no valid rep ({a["n_reps"]} rejected/invalid)')
                for inv in a['invalid']:
                    print(f'           rerun {os.path.basename(inv["run"].rstrip("/"))}/{inv["slot"]}: {"; ".join(inv["reasons"])[:120]}')
                continue
            d, p = cpu['delta'], cpu['delta_pct']
            star = '*' if cpu['below_resolution_all'] else ''
            print(f'  {cell:<8} {arm:>4} {a["bucket"] or "-":>6} {cpu["baseline"]["median"]:>12.3f} '
                  f'{d["median"] * 1000:>+8.0f} [{d["min"] * 1000:+.0f},{d["max"] * 1000:+.0f}]{star:<1} '
                  f'{p["median"]:>+7.2f} [{p["min"]:+.2f},{p["max"]:+.2f}]{star:<1} '
                  f'{fmt((cpu["band_pct"] or {}).get("max"), ".2f"):>7} {a["n_valid"]:>2}/{a["n_reps"]:<2} '
                  f'{fmt((a["spans_per_req"] or {}).get("median"), ".2f"):>9} '
                  f'{fmt((a["keep_rate"] or {}).get("median"), ".2f"):>5} '
                  f'{fmt((a["odiglet_us_per_span"] or {}).get("median"), ".1f"):>15}')
            for inv in a['invalid']:
                print(f'           rerun {os.path.basename(inv["run"].rstrip("/"))}/{inv["slot"]}: {"; ".join(inv["reasons"])[:120]}')
    print('  * = below the drift band of every valid rep')


def print_model(label, model, arm):
    if not model or model.get('c_span_us') is None:
        return
    hw = f' +- {model["c_span_hw_us"]:.1f}' if model.get('c_span_hw_us') is not None else ''
    print(f'\n=== {label} model at {arm}%: delta_ms = spans/req x c_span,  c_span = {model["c_span_us"]:.1f}{hw} us ===')
    for p in model['points']:
        ci = ''
        if p.get('us_per_span_lo') is not None:
            ci = f' [{p["us_per_span_lo"]:.0f}, {p["us_per_span_hi"]:.0f}]'
        flag = ''
        if p.get('resolved') is False or p.get('below_resolution'):
            flag = '  *not resolved'
        print(f'  {p["cell"]:<8} spans {p["spans_per_req"]:>5.2f}  measured {p["delta_ms"] * 1000:>+7.0f} us  '
              f'predicted {p["predicted_ms"] * 1000:>+7.0f} us  residual {p["residual_ms"] * 1000:>+6.0f} us  '
              f'({p["us_per_span"]:.0f}{ci} us/span){flag}')
    w = model.get('reference_point')
    if not w:
        return
    print(f'  reference: {w["label"]} {w["cpu_ms"]} ms, {w["spans_per_req"]} spans, +{w["delta_ms"]} ms '
          f'-> {w["us_per_span"]:.0f} us/span')


def main():
    args = sys.argv[1:]
    out_json = None
    if '--json' in args:
        i = args.index('--json')
        out_json = args[i + 1]
        del args[i:i + 2]
    runs = [a for a in args if not a.startswith('--')]
    if not runs:
        sys.exit(__doc__)

    reps, notes = {}, []
    for run in runs:
        loaded, run_notes = load_run(run)
        notes += run_notes
        for cell, rep in loaded.items():
            reps.setdefault(cell, []).append(rep)
            print_rep(cell, rep)
    if notes:
        print('\n=== notes ===')
        for n in notes:
            print('  ' + n)
    if not reps:
        sys.exit('no usable arms in any run directory')

    cells = {}
    for cell, rs in sorted(reps.items()):
        cells[cell] = {'preset': rs[0]['preset'], 'expect_spans': rs[0]['expect_spans'],
                       'rate': rs[0]['rate'], 'nodes': sorted({n for r in rs for n in r['nodes']}),
                       'reps': rs, 'aggregate': aggregate(rs), 'pooled': pooled(rs),
                       'paired': paired(rs)}
    print_paired(cells)
    print_pooled(cells)
    print_aggregate(cells)

    model_paired = {arm: fit_model_paired(cells, arm) for arm in ('100', '25')}
    model_pooled = {arm: fit_model_pooled(cells, arm) for arm in ('100', '25')}
    model_bracket = {arm: fit_model_bracket(cells, arm) for arm in ('100', '25')}
    for arm in ('100', '25'):
        print_model('paired', model_paired[arm], arm)
    for arm in ('100', '25'):
        print_model('pooled', model_pooled[arm], arm)
    for arm in ('100', '25'):
        print_model('bracketed', model_bracket[arm], arm)

    summary = {'runs': runs, 'notes': notes, 'band_limit_pct': BAND_LIMIT_PCT,
               'buckets': [{'name': n, 'lo_ms': lo, 'hi_ms': hi if hi != float('inf') else None} for n, lo, hi in BUCKETS],
               'cells': cells, 'model': model_bracket, 'model_pooled': model_pooled,
               'model_paired': model_paired}
    out_json = out_json or os.path.join(runs[0], 'summary.json')
    with open(out_json, 'w') as fh:
        json.dump(summary, fh, indent=2, default=str)
    print('\nwrote', out_json)


if __name__ == '__main__':
    main()
