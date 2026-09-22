#!/usr/bin/env python3
"""Accept or reject a measured arm before it is allowed into a result.

Every rule here exists because the defect it describes already corrupted a run:

  errors      8/8 uninstrumented arms in the paired matrix hit the 60s client
              timeout while every instrumented arm stayed under 2.6s. The error
              rate looked harmless (0.09-0.45%) so nothing rejected it, and the
              hanging baseline got reported as an 80% instrumented speedup.
  timeouts    the same defect seen from the latency side: a max at the client
              timeout means requests parked, not that the service was slow.
  throttling  a cgroup-throttled arm measures the limit, not the workload.
  dropped     dropped iterations mean the load generator, not the workload,
              was the bottleneck.

Rules added for the bucket benchmark, each guarding one assumption of the
method (an exclusive core, an identical settled JVM, the configuration that
was asked for, the request shape that was designed):

  offered     achieved/offered < 0.99: the arm did not run at its rate.
  qos         pod not Guaranteed: no exclusive core, CPU borrowed per run.
  cpu.max     a CFS quota smaller than the pod's exclusive cpuset: the quota
              can bind, so the pod can be throttled. Kubernetes 1.33+ drops the
              quota for exclusive-CPU pods; older versions keep one equal to the
              CPU limit, which cannot bind on a cpuset of the same size and is
              accepted (throttling is still checked on its own).
  cpuset      not exactly 2 CPUs: not one physical core.
  pod         restart or a different pod at the end: a cold JVM was measured.
  halves      |h1-h2|/mean > 5%: the arm was still settling. A JIT-cold arm
              shows 9-15%. Warm arms at ~60% of the core still drift 2-4%
              across an 8-minute window (C2 keeps re-optimising at 0.5-0.75 s
              of compile time per minute); that drift repeats identically in
              every arm because every arm restarts and warms on the same
              schedule, so it cancels in the delta, and the off-off drift band
              reports whatever does not.
  ic.yaml     instrumented arm without an InstrumentationConfig, or off arm
              with one: the arm ran the wrong configuration.
  checks      any response with the wrong ops/knobs: a different application.
  spans       100% arm spans/req outside +-10% of design: partial coverage or
              dropped spans; the per-span cost would be wrong.
  keep        25% arm keep-rate outside 20-28%: sampling not applied as asked.
  jit         compilation > 1000 ms/min: the JIT was still cold. A JIT-cold
              JVM compiles 1.5-5 s/min; warm arms on this workload trickle
              100-750 ms/min indefinitely, scaling with the CPU work per request.

Legacy call (two positionals) applies the first four rules only. With --arm the
new rules apply and a missing evidence file is itself a rejection.

    gate.py <k6.json> <cpu.json> [--arm off|100|25] [--meta m.json] [--h1 h1.json --h2 h2.json]
            [--sink sink.json] [--jvm jvm.json] [--ic ic.yaml] [--expect-spans N]

Exit code is nonzero if the arm must not be used.
"""

import json
import os
import sys

TIMEOUT_MS = float(os.environ.get('CLIENT_TIMEOUT_MS', '60000'))
MAX_ERR_PCT = float(os.environ.get('MAX_ERR_PCT', '0.10'))
MAX_DROP_FRAC = float(os.environ.get('MAX_DROP_FRAC', '0.005'))
MIN_ACHIEVED = float(os.environ.get('MIN_ACHIEVED', '0.99'))
MAX_HALF_GAP_PCT = float(os.environ.get('MAX_HALF_GAP_PCT', '5.0'))
SPANS_TOL = float(os.environ.get('SPANS_TOL', '0.10'))
KEEP_LO = float(os.environ.get('KEEP_LO', '0.20'))
KEEP_HI = float(os.environ.get('KEEP_HI', '0.28'))
MAX_JIT_MS_PER_MIN = float(os.environ.get('MAX_JIT_MS_PER_MIN', '1000'))
EXPECT_CPUSET = int(os.environ.get('EXPECT_CPUSET', '2'))


def present(path):
    return bool(path) and os.path.exists(path) and os.path.getsize(path) > 0


def load(path):
    return json.load(open(path))


def legacy_rules(k, c):
    reasons = []
    o = k['overall']

    err = (o.get('failed_rate') or 0) * 100
    if err > MAX_ERR_PCT:
        reasons.append(f'error rate {err:.3f}% > {MAX_ERR_PCT}%')

    mx = max([s.get('max_ms', 0) or 0 for s in k.get('scenarios', {}).values()] or [0])
    if mx >= 0.95 * TIMEOUT_MS:
        reasons.append(f'max latency {mx:.0f}ms at client timeout {TIMEOUT_MS:.0f}ms - '
                       f'requests hung, arm is not a valid baseline')

    # A constant-arrival-rate executor drops a handful of iterations while it
    # allocates its VU pool at startup; at 3000/s that is ~0.08% and does not
    # bias a five-minute steady state. A generator that genuinely cannot keep up
    # drops percent-scale, which this still catches.
    dropped = o.get('dropped_iterations') or 0
    total = (o.get('requests') or 0) + dropped
    if total and dropped / total > MAX_DROP_FRAC:
        reasons.append(f'{dropped} dropped iterations ({dropped / total * 100:.2f}%) - '
                       f'load generator could not keep up')

    if c and c.get('nr_throttled_delta', 0) > 0:
        share = c.get('throttled_usec_delta', 0) / max(c.get('window_ns', 1) / 1000, 1)
        if share > 0.001:
            reasons.append(f'cgroup throttled {c["nr_throttled_delta"]} periods '
                           f'({share * 100:.2f}% of window)')
    return reasons, err, mx


def strict_rules(k, c, arm, meta, h1, h2, sink, jvm, ic_text, expect_spans):
    reasons = []
    o = k['overall']
    instrumented = arm not in (None, 'off')

    offered = float(k.get('rate') or 0)
    achieved = o.get('tps') or 0
    if offered and achieved / offered < MIN_ACHIEVED:
        reasons.append(f'achieved {achieved:.1f} req/s is {achieved / offered * 100:.1f}% of offered '
                       f'{offered:.0f} (< {MIN_ACHIEVED * 100:.0f}%)')

    if (o.get('check_fail') or 0) > 0:
        reasons.append(f'{o["check_fail"]} responses failed the ops/knobs check - '
                       f'a different application shape than designed')

    if meta:
        if meta.get('qos') and meta['qos'] != 'Guaranteed':
            reasons.append(f'pod QoS {meta["qos"]} != Guaranteed')
        if meta.get('pod') and meta.get('pod_end') and meta['pod'] != meta['pod_end']:
            reasons.append(f'pod changed during arm: {meta["pod"]} -> {meta["pod_end"]}')
        rs, re_ = meta.get('restarts_start'), meta.get('restarts_end')
        if rs is not None and re_ is not None and re_ != rs:
            reasons.append(f'container restarted during arm ({rs} -> {re_})')
        if meta.get('reset_http') not in (None, 200, '200'):
            reasons.append(f'/admin/reset returned {meta["reset_http"]}')

    if c:
        cm = c.get('cpu_max')
        if cm is None:
            reasons.append('cpu.max not recorded')
        elif cm.split()[0] != 'max':
            try:
                quota, period = (int(x) for x in cm.split()[:2])
                cpus = quota / period
            except (ValueError, ZeroDivisionError):
                cpus = 0.0
            size = c.get('cpuset_size') or 0
            if not size or cpus < size:
                reasons.append(f'CFS quota of {cpus:.2f} CPUs (cpu.max = "{cm}") is below the '
                               f'{size}-CPU cpuset - the quota can throttle the pod')
        if c.get('cpuset') is None:
            reasons.append('cpuset.cpus.effective not recorded')
        elif c.get('cpuset_size') != EXPECT_CPUSET:
            reasons.append(f'cpuset {c["cpuset"]} has {c.get("cpuset_size")} CPUs, expected {EXPECT_CPUSET}')
        elif c.get('cpuset_start') and c['cpuset_start'] != c['cpuset']:
            reasons.append(f'cpuset changed during arm: {c["cpuset_start"]} -> {c["cpuset"]}')
    else:
        reasons.append('no cpu.json')

    if h1 and h2:
        a, b = h1.get('cpu_ms_per_req'), h2.get('cpu_ms_per_req')
        if a and b:
            gap = abs(a - b) / ((a + b) / 2) * 100
            if gap > MAX_HALF_GAP_PCT:
                reasons.append(f'half-window CPU/req differ by {gap:.2f}% ({a:.4f} vs {b:.4f}) - not settled')
    else:
        reasons.append('half-window cpu files missing')

    if ic_text is None:
        reasons.append('ic.yaml missing')
    else:
        has_ic = 'kind: InstrumentationConfig' in ic_text
        if instrumented and not has_ic:
            reasons.append('instrumented arm but no InstrumentationConfig delivered')
        if instrumented and has_ic and 'agentEnabled: true' not in ic_text:
            reasons.append('InstrumentationConfig present but agentEnabled is not true')
        if not instrumented and has_ic:
            reasons.append('off arm but an InstrumentationConfig exists (Source still present)')
        if instrumented and arm != '100' and f'percentageAtMost: {arm}' not in ic_text:
            reasons.append(f'sampling arm {arm} but ic.yaml carries no percentageAtMost: {arm}')
        if arm == '100' and 'percentageAtMost' in ic_text:
            reasons.append('100% arm but a head-sampling rule was delivered')

    if instrumented:
        if not sink:
            reasons.append('no sink delta on an instrumented arm')
        else:
            if (sink.get('receiver_refused_delta') or 0) > 0 or (sink.get('exporter_failed_delta') or 0) > 0:
                reasons.append(f'sink backpressure: refused {sink.get("receiver_refused_delta")}, '
                               f'export failed {sink.get("exporter_failed_delta")}')
            spr = sink.get('spans_per_req')
            if spr is None:
                reasons.append('sink delta has no spans_per_req')
            elif expect_spans:
                if arm == '100' and abs(spr - expect_spans) > SPANS_TOL * expect_spans:
                    reasons.append(f'{spr:.2f} spans/req at 100%, design is {expect_spans} '
                                   f'(+-{SPANS_TOL * 100:.0f}%)')
                if arm != '100':
                    keep = spr / expect_spans
                    if not (KEEP_LO <= keep <= KEEP_HI):
                        reasons.append(f'keep-rate {keep * 100:.1f}% at {arm}% sampling, '
                                       f'expected {KEEP_LO * 100:.0f}-{KEEP_HI * 100:.0f}%')
    elif sink and (sink.get('spans_per_req') or 0) > 0.01:
        reasons.append(f'off arm but the sink received {sink["spans_per_req"]:.2f} spans/req')

    if not jvm:
        reasons.append('no jvm delta')
    else:
        rate = jvm.get('compile_ms_per_min')
        if rate is not None and rate > MAX_JIT_MS_PER_MIN:
            reasons.append(f'JIT compiled {rate:.0f} ms/min during the window (> {MAX_JIT_MS_PER_MIN:.0f}) - not warm')
    return reasons


def gate(k6_path, cpu_path, arm=None, meta_path=None, h1_path=None, h2_path=None,
         sink_path=None, jvm_path=None, ic_path=None, expect_spans=None, strict=False):
    if not present(k6_path):
        return {'ok': False, 'reasons': ['no k6 summary (job produced no output)']}

    k = load(k6_path)
    c = load(cpu_path) if present(cpu_path) else None
    reasons, err, mx = legacy_rules(k, c)

    if strict:
        meta = load(meta_path) if present(meta_path) else None
        h1 = load(h1_path) if present(h1_path) else None
        h2 = load(h2_path) if present(h2_path) else None
        sink = load(sink_path) if present(sink_path) else None
        jvm = load(jvm_path) if present(jvm_path) else None
        ic_text = open(ic_path).read() if present(ic_path) else None
        reasons += strict_rules(k, c, arm, meta, h1, h2, sink, jvm, ic_text, expect_spans)

    return {'ok': not reasons, 'reasons': reasons, 'error_pct': err, 'max_ms': mx,
            'arm': arm, 'strict': strict}


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
    if len(pos) < 2:
        sys.exit(__doc__)
    v = gate(pos[0], pos[1], arm=opts.get('arm'), meta_path=opts.get('meta'),
             h1_path=opts.get('h1'), h2_path=opts.get('h2'), sink_path=opts.get('sink'),
             jvm_path=opts.get('jvm'), ic_path=opts.get('ic'),
             expect_spans=float(opts['expect-spans']) if opts.get('expect-spans') else None,
             strict='arm' in opts)
    print(json.dumps(v, indent=2))
    sys.exit(0 if v['ok'] else 1)


if __name__ == '__main__':
    main()
