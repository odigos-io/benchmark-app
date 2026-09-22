#!/usr/bin/env python3
"""Check the analysis against known answers.

Builds synthetic repetitions with a planted per-span cost and a planted amount
of between-window variation, runs the real analyzer over them, and checks two
things that the published report depends on:

  coverage   the planted value lies inside the 95% confidence interval the
             analysis reports. If this fails the analysis is understating its
             own uncertainty, which is the one error a reader cannot detect.
  recovery   where the effect is large against the noise, the point estimate
             lands on it.

Two shapes are exercised, because the right baseline estimator depends on which
one is real. On the reference cluster two uninstrumented windows of one JVM
differ by -1.9% to +2.0% with no consistent sign, so the windows are averaged.
A JVM that genuinely trends wants the trend removed instead, which is available
behind BASELINE_FIT and is checked here on a fixture built that way.

    python3 analysis/selftest.py          # exits non-zero on failure
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOL_US = 60          # point estimate tolerance where the effect is resolvable
C_SPAN_US = 75.0     # must match make_fixture.py
SAMPLED_FRACTION = 0.55
SPANS = {'s': 8, 'schatty': 14, 'm': 8, 'l': 8, 'xl': 8}


def scenario(label, fixture_args, env):
    tmp = tempfile.mkdtemp(prefix='bucket-selftest-')
    try:
        subprocess.run([sys.executable, os.path.join(HERE, 'make_fixture.py'), tmp, *fixture_args],
                       check=True, capture_output=True)
        runs = [os.path.join(tmp, d) for d in sorted(os.listdir(tmp))]
        out = os.path.join(tmp, 'summary.json')
        subprocess.run([sys.executable, os.path.join(HERE, 'analyze.py'), *runs, '--json', out],
                       check=True, capture_output=True, env={**os.environ, **env})
        summary = json.load(open(out))

        failures, covered, recovered = [], 0, 0
        for cell, c in summary['cells'].items():
            arms = (c.get('paired') or {}).get('arms') or {}
            for arm, factor in (('100', 1.0), ('25', SAMPLED_FRACTION)):
                d = (arms.get(arm) or {}).get('cpu_ms_per_req')
                if not d:
                    failures.append(f'{cell} @{arm}%: no paired delta')
                    continue
                want_us = C_SPAN_US * SPANS[cell] * factor
                got_us = d['delta'] * 1000
                lo, hi = d.get('ci_lo'), d.get('ci_hi')
                if lo is None:
                    failures.append(f'{cell} @{arm}%: no interval at n={d["n"]}')
                    continue
                lo, hi = lo * 1000, hi * 1000
                covered += 1
                if not (lo <= want_us <= hi):
                    failures.append(f'{cell} @{arm}%: planted {want_us:+.0f} us outside the '
                                    f'reported interval [{lo:+.0f}, {hi:+.0f}]')
                # Only demand a close point estimate where the effect is bigger
                # than the interval it is quoted with.
                if abs(want_us) > (hi - lo) / 2:
                    recovered += 1
                    if abs(got_us - want_us) > TOL_US:
                        failures.append(f'{cell} @{arm}%: recovered {got_us:+.0f} us, '
                                        f'planted {want_us:+.0f} us')

        # The pooled view ignores which JVM a number came from. On a fixture with
        # a systematic trend it must visibly fail; if it ever stops failing, the
        # fixture no longer exercises the thing the paired view exists for.
        if '--drift' in fixture_args:
            pooled_ok = sum(
                1 for cell, c in summary['cells'].items()
                for d in [(c.get('pooled') or {}).get('deltas', {}).get('100', {}).get('cpu_ms_per_req')]
                if d and d.get('resolved')
                and abs(d['delta'] * 1000 - C_SPAN_US * SPANS[cell]) < TOL_US)
            if pooled_ok == len(summary['cells']):
                failures.append('the pooled view recovered every planted delta, so this fixture no '
                                'longer contains the drift the paired view exists to remove')

        for f in failures:
            print(f'FAIL [{label}]', f)
        print(f'{label}: {covered} intervals checked for coverage, {recovered} point estimates '
              f'checked to {TOL_US} us, {len(failures)} failures')
        return len(failures)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    bad = 0
    bad += scenario('between-window noise, averaged baseline', [], {})
    bad += scenario('systematic drift, fitted baseline', ['--drift', '-1.2'], {'BASELINE_FIT': '1'})
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
