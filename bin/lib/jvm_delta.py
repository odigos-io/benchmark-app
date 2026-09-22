#!/usr/bin/env python3
"""What the JVM did during the measured window, from /actuator/prometheus.

Two scrapes (timestamp + Micrometer text). Counters become deltas; heap use is
a level and is reported at both ends plus the larger of the two. The
compilation rate is the warmth check: a JIT still producing code was still
changing the thing being measured.

thread_cpu_ms_per_req is the app's own estimate: CPU time of the request
threads (bench_thread_cpu_seconds_total, ThreadMXBean) over the requests they
served. It excludes JIT, GC and other JVM threads, so it sits below the cgroup
figure; the agent's uprobe/JNI work runs on the request thread and shows up
here as well.

    python3 jvm_delta.py <before> <after>
"""

import json
import re
import sys

LINE = re.compile(r'^([A-Za-z_:][A-Za-z0-9_:]*)(\{([^}]*)\})?\s+([-+0-9.eEnaNif]+)')
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
MB = 1024 * 1024


def parse(path):
    ts, metrics = None, []
    for ln in open(path):
        ln = ln.strip()
        if not ln or ln.startswith('#') or ln.startswith('@@'):
            continue
        if ln.startswith('TS='):
            ts = int(ln[3:])
            continue
        m = LINE.match(ln)
        if not m:
            continue
        try:
            v = float(m.group(4))
        except ValueError:
            continue
        metrics.append((m.group(1), dict(LABEL.findall(m.group(3) or '')), v))
    return ts, metrics


def total(metrics, name, where=None):
    s, seen = 0.0, False
    for n, labels, v in metrics:
        if n != name:
            continue
        if where and any(labels.get(k) != val for k, val in where.items()):
            continue
        s += v
        seen = True
    return s if seen else None


def delta(a, b):
    return None if a is None or b is None else b - a


def rnd(v, nd):
    return None if v is None else round(v, nd)


def compute(start_path, end_path):
    (ta, ma), (tb, mb) = parse(start_path), parse(end_path)
    window_s = (tb - ta) / 1e9 if ta and tb else None

    gc_s = delta(total(ma, 'jvm_gc_pause_seconds_sum'), total(mb, 'jvm_gc_pause_seconds_sum'))
    gc_n = delta(total(ma, 'jvm_gc_pause_seconds_count'), total(mb, 'jvm_gc_pause_seconds_count'))
    alloc = delta(total(ma, 'jvm_gc_memory_allocated_bytes_total'),
                  total(mb, 'jvm_gc_memory_allocated_bytes_total'))
    comp = delta(total(ma, 'jvm_compilation_time_ms_total'), total(mb, 'jvm_compilation_time_ms_total'))
    heap_a = total(ma, 'jvm_memory_used_bytes', {'area': 'heap'})
    heap_b = total(mb, 'jvm_memory_used_bytes', {'area': 'heap'})
    heap_max = total(mb, 'jvm_memory_max_bytes', {'area': 'heap'})
    reqs = delta(total(ma, 'bench_requests_total'), total(mb, 'bench_requests_total'))
    thread_cpu = delta(total(ma, 'bench_thread_cpu_seconds_total'), total(mb, 'bench_thread_cpu_seconds_total'))
    threads = total(mb, 'jvm_threads_live_threads')

    heaps = [x for x in (heap_a, heap_b) if x is not None]
    return {
        'window_s': rnd(window_s, 3),
        'gc_pause_s': rnd(gc_s, 4),
        'gc_count': gc_n,
        'gc_pause_pct': rnd(gc_s / window_s * 100, 4) if gc_s is not None and window_s else None,
        'alloc_mb': rnd(alloc / MB, 1) if alloc is not None else None,
        'alloc_mb_per_s': rnd(alloc / MB / window_s, 2) if alloc is not None and window_s else None,
        'alloc_mb_per_req': rnd(alloc / MB / reqs, 4) if alloc is not None and reqs else None,
        'compile_ms': comp,
        'compile_ms_per_min': rnd(comp / window_s * 60, 2) if comp is not None and window_s else None,
        'heap_used_mb_start': rnd(heap_a / MB, 1) if heap_a is not None else None,
        'heap_used_mb_end': rnd(heap_b / MB, 1) if heap_b is not None else None,
        'heap_used_mb_max': rnd(max(heaps) / MB, 1) if heaps else None,
        'heap_max_mb': rnd(heap_max / MB, 1) if heap_max is not None else None,
        'threads_live': threads,
        'bench_requests_delta': reqs,
        'thread_cpu_s': rnd(thread_cpu, 4),
        'thread_cpu_ms_per_req': rnd(thread_cpu * 1000 / reqs, 5) if thread_cpu is not None and reqs else None,
    }


def main():
    json.dump(compute(sys.argv[1], sys.argv[2]), sys.stdout, indent=2)
    print()


if __name__ == '__main__':
    main()
