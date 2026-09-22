#!/usr/bin/env python3
"""CPU used by the workload container, from its own cgroup counter.

Reads two snapshots taken inside the container, each one a container-local
timestamp followed by cgroup v2 cpu.stat. Both the counter and the clock come
from the same place, so the measurement window is exactly the interval the
counter covers.

This replaces reading cAdvisor from the node and dividing by a wall clock
measured on the laptop driving the run. That combination charged each arm a
window that was wrong by -8.2s to +10.8s, with the error biased per arm: one
arm was charged 17s of idle time it never served.

The snapshot may also carry mem_current / mem_peak (memory levels), cpu_max
(the CFS quota line, "max 100000" when exclusive CPUs disabled it) and cpuset
(cpuset.cpus.effective); these are passed through for the gate.

    python3 cgroup_delta.py <start-snap> <end-snap> <requests>
    python3 cgroup_delta.py <start-snap> <end-snap> tps=<achieved req/s>

The second form is for a window that lies strictly inside a constant-arrival
measure phase: the requests served in the window are the achieved rate times
the window, which is exact to the rate jitter the gate already bounds.
"""

import json
import sys

FIELDS = ('usage_usec', 'user_usec', 'system_usec', 'nr_throttled', 'throttled_usec',
          'nr_periods', 'mem_current', 'mem_peak')
TEXT_FIELDS = ('cpu_max', 'cpuset')


def parse(path):
    out = {}
    with open(path) as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    for ln in lines:
        if ln.startswith('TS='):
            out['ts_ns'] = int(ln[3:])
            continue
        parts = ln.split()
        if len(parts) == 2 and parts[0] in FIELDS:
            out[parts[0]] = int(parts[1])
        elif parts and parts[0] in TEXT_FIELDS:
            out[parts[0]] = ' '.join(parts[1:])
    return out


def cpuset_size(spec):
    n = 0
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            lo, hi = part.split('-', 1)
            n += int(hi) - int(lo) + 1
        else:
            n += 1
    return n


def main():
    a, b = parse(sys.argv[1]), parse(sys.argv[2])
    window_ns = b['ts_ns'] - a['ts_ns']
    cpu_usec = b['usage_usec'] - a['usage_usec']

    spec = sys.argv[3] if len(sys.argv) > 3 else '0'
    if spec.startswith('tps='):
        tps = float(spec[4:])
        requests = int(round(tps * window_ns / 1e9))
        source = f'tps {tps:.3f} x window'
    else:
        requests = int(spec)
        source = 'count'

    out = {
        'requests_source': source,
        'window_ns': window_ns,
        'window_s': round(window_ns / 1e9, 3),
        'cpu_seconds': round(cpu_usec / 1e6, 4),
        'cpu_cores_avg': round(cpu_usec * 1000 / window_ns, 4) if window_ns else None,
        'user_seconds': round((b['user_usec'] - a['user_usec']) / 1e6, 4),
        'system_seconds': round((b['system_usec'] - a['system_usec']) / 1e6, 4),
        'nr_throttled_delta': b.get('nr_throttled', 0) - a.get('nr_throttled', 0),
        'throttled_usec_delta': b.get('throttled_usec', 0) - a.get('throttled_usec', 0),
        'requests': requests,
        'cpu_ms_per_req': round(cpu_usec / 1000 / requests, 5) if requests else None,
    }

    # Memory is a level, not a counter: report where it ended and the high-water
    # mark, not a difference. A delta across the window would mostly measure where
    # the heap happened to be in its GC cycle at each end.
    if b.get('mem_current'):
        out['mem_end_mb'] = round(b['mem_current'] / 1024 / 1024, 1)
        out['mem_start_mb'] = round(a.get('mem_current', 0) / 1024 / 1024, 1)
    if b.get('mem_peak'):
        out['mem_peak_mb'] = round(b['mem_peak'] / 1024 / 1024, 1)
    if b.get('cpu_max'):
        out['cpu_max'] = b['cpu_max']
        out['cpu_max_start'] = a.get('cpu_max')
    if b.get('cpuset'):
        out['cpuset'] = b['cpuset']
        out['cpuset_start'] = a.get('cpuset')
        out['cpuset_size'] = cpuset_size(b['cpuset'])
    json.dump(out, sys.stdout, indent=2)
    print()


if __name__ == '__main__':
    main()
