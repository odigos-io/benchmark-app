#!/usr/bin/env python3
"""CPU of the agent, the node collector and the whole node during one arm.

Two snapshots taken by the node-probe pod on the app node, each a timestamp,
the root cgroup's cpu.stat ("@@ root"), the cpu.stat of every Odigos pod on
that node ("@@ pod <name>") and of each container scope inside it
("@@ ctr <pod> <container-id>"), plus the pod's container name -> id map
("@@ ctrmap <pod> <name>=<id>"). In Odigos 1.36 the odiglet pod holds the
agent (`odiglet`), the node collector (`data-collection`) and the device
plugin as three containers, so the split is by container name; the pod total
stays as a cross-check that the container scopes account for the pod. The
agent's cost is thereby measured where it is paid, on the node, separately
from the app container the main figure comes from; with the cell's span
count it becomes a per-span cost.

    python3 node_delta.py <start> <end> [--spans N] [--requests N]
    python3 node_delta.py <start> <end> --tps <achieved req/s> --spans-per-req <x>

The second form derives requests = tps x window and spans = spans/req x
requests, for a window that lies strictly inside the constant-rate measure phase.
"""

import json
import sys

CONTAINER_ROLES = {'odiglet': 'odiglet', 'data-collection': 'node_collector'}
ROLE_KEY = {'odiglet': 'odiglet_cores', 'node_collector': 'node_collector_cores'}


def parse(path):
    ts, sections, ctrmap, cur = None, {}, {}, None
    for ln in open(path):
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith('TS='):
            ts = int(ln[3:])
            continue
        if ln.startswith('@@ ctrmap '):
            _, _, pod, pair = ln.split(None, 3)
            name, _, cid = pair.partition('=')
            ctrmap.setdefault(pod, {})[cid] = name
            cur = None
            continue
        if ln.startswith('@@'):
            cur = ln[2:].strip()
            sections[cur] = {}
            continue
        p = ln.split()
        if cur is not None and len(p) == 2 and p[1].lstrip('-').isdigit():
            sections[cur][p[0]] = int(p[1])
    return ts, sections, ctrmap


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
    (ta, sa, _), (tb, sb, ctrmap) = parse(pos[0]), parse(pos[1])
    window_ns = tb - ta if ta and tb else None
    spans = float(opts.get('spans', 0) or 0)
    requests = float(opts.get('requests', 0) or 0)
    if opts.get('tps') and window_ns:
        requests = float(opts['tps']) * window_ns / 1e9
        if opts.get('spans-per-req'):
            spans = float(opts['spans-per-req']) * requests

    def cores(key):
        a, b = sa.get(key, {}), sb.get(key, {})
        if 'usage_usec' not in a or 'usage_usec' not in b or not window_ns:
            return None
        return (b['usage_usec'] - a['usage_usec']) * 1000 / window_ns

    # Odigos containers are Burstable with CPU limits; a throttled agent or
    # collector during the window is delayed export, and is reported per container.
    def throttled(key):
        a, b = sa.get(key, {}), sb.get(key, {})
        if 'throttled_usec' not in a or 'throttled_usec' not in b:
            return None, None
        return (b['nr_throttled'] - a['nr_throttled'], (b['throttled_usec'] - a['throttled_usec']) / 1000)

    out = {'window_s': round(window_ns / 1e9, 3) if window_ns else None,
           'root_cores_avg': None, 'pods': {}, 'odiglet_cores': 0.0, 'node_collector_cores': 0.0,
           'other_odigos_cores': 0.0}
    r = cores('root')
    out['root_cores_avg'] = round(r, 4) if r is not None else None

    for key in sb:
        if not key.startswith('pod '):
            continue
        pod = key.split(None, 1)[1]
        c = cores(key)
        out['pods'][pod] = {'cores_avg': round(c, 5) if c is not None else None, 'containers': {},
                            'containers_sum_cores': 0.0}
    for key in sb:
        if not key.startswith('ctr '):
            continue
        _, pod, cid = key.split(None, 2)
        c = cores(key)
        name = ctrmap.get(pod, {}).get(cid, 'sandbox' if cid not in ctrmap.get(pod, {}) else cid)
        role = CONTAINER_ROLES.get(name, 'other')
        entry = out['pods'].setdefault(pod, {'cores_avg': None, 'containers': {}, 'containers_sum_cores': 0.0})
        nr, ms = throttled(key)
        entry['containers'][name] = {'id': cid[:12], 'role': role,
                                     'cores_avg': round(c, 5) if c is not None else None,
                                     'nr_throttled': nr, 'throttled_ms': round(ms, 1) if ms is not None else None}
        if c is not None:
            entry['containers_sum_cores'] += c
            out[ROLE_KEY.get(role, 'other_odigos_cores')] += c
        if role in ROLE_KEY and ms is not None:
            out[role + '_throttled_ms'] = round(out.get(role + '_throttled_ms', 0.0) + ms, 1)
    for entry in out['pods'].values():
        entry['containers_sum_cores'] = round(entry['containers_sum_cores'], 5)
        if entry['cores_avg'] and entry['containers_sum_cores']:
            entry['containers_cover_pct'] = round(entry['containers_sum_cores'] / entry['cores_avg'] * 100, 1)
    for k in ('odiglet_cores', 'node_collector_cores', 'other_odigos_cores'):
        out[k] = round(out[k], 5)
    if spans and window_ns:
        out['spans'] = spans
        out['odiglet_us_per_span'] = round(out['odiglet_cores'] * window_ns / 1000 / spans, 3)
        out['node_collector_us_per_span'] = round(out['node_collector_cores'] * window_ns / 1000 / spans, 3)
    if requests and window_ns:
        out['requests'] = requests
        out['odiglet_us_per_req'] = round(out['odiglet_cores'] * window_ns / 1000 / requests, 3)
    json.dump(out, sys.stdout, indent=2)
    print()


if __name__ == '__main__':
    main()
