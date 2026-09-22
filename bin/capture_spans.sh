#!/usr/bin/env bash
# Trace shape per cell, from the exported spans themselves.
#
# The per-span cost model rests on each request producing exactly the designed
# spans (1 server + 3 JDBC + 2 Redis + 1 HTTP client, or 1+5+4+2 for S-chatty).
# This drives a short load through one cell at a time with a second Destination
# pointed at a file-writing collector, and counts spans per trace and per kind.
#
#   CELLS="s schatty" bin/capture_spans.sh      (Sources must be present: run
#                                                with the 100% arm applied)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/bin/lib/cells.sh"
KUBECTL=(kubectl)
[ -n "${KUBE_CONTEXT:-}" ] && KUBECTL+=(--context "$KUBE_CONTEXT")
CELLS="${CELLS:-$(cells_all)}"
DUR="${DUR:-90s}"
OUT="${OUT:-$ROOT/results/spans-$(date +%m%d-%H%M%S)}"
mkdir -p "$OUT"

for c in $CELLS; do require_cell "$c" || exit 1; done

"${KUBECTL[@]}" apply -f "$ROOT/k8s/sink/trace-capture.yaml" >/dev/null
"${KUBECTL[@]}" apply -f "$ROOT/k8s/sink/destination-capture.yaml" >/dev/null
trap '"${KUBECTL[@]}" delete -f "$ROOT/k8s/sink/destination-capture.yaml" --ignore-not-found >/dev/null 2>&1' EXIT
"${KUBECTL[@]}" -n bench-runner create configmap k6-script --from-file=load.js="$ROOT/k6/load.js" \
  --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f - >/dev/null
sleep 20

for c in $CELLS; do
  # One cell at a time: the capture file is shared, so concurrent load would
  # mix cells and make spans-per-request meaningless.
  "${KUBECTL[@]}" -n bench-sink rollout restart deploy/trace-capture >/dev/null
  "${KUBECTL[@]}" -n bench-sink rollout status deploy/trace-capture --timeout=5m >/dev/null
  sleep 10

  job="k6-tr-$c"
  "${KUBECTL[@]}" -n bench-runner delete job "$job" --ignore-not-found >/dev/null 2>&1
  JOB_NAME="$job" BASE_URL="http://bucket-app.cell-$c.svc.cluster.local:8080" \
  K6_MODE=rate K6_DURATION="$DUR" K6_WARM_RAMP=60s K6_WARM_S=0 K6_RATE="$(rate_of "$c")" K6_P99_S=0.5 \
  K6_PRE_VUS="" K6_MAX_VUS="" K6_EXPECT_UNITS="" K6_EXPECT_JDBC="$(jdbc_of "$c")" \
  K6_EXPECT_REDIS="$(redis_of "$c")" K6_EXPECT_HTTP="$(http_of "$c")" \
  K6_CPU_REQ="$(k6cpu_of "$c")" K6_CPU_LIM="$(k6cpu_of "$c")" \
    envsubst < "$ROOT/k8s/runner/k6-job.tpl.yaml" | "${KUBECTL[@]}" apply -f - >/dev/null

  deadline=$(( $(date +%s) + 600 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    s=$("${KUBECTL[@]}" -n bench-runner get job "$job" -o jsonpath='{.status.succeeded}' 2>/dev/null)
    f=$("${KUBECTL[@]}" -n bench-runner get job "$job" -o jsonpath='{.status.failed}' 2>/dev/null)
    { [ "${s:-0}" -ge 1 ] || [ "${f:-0}" -ge 1 ]; } 2>/dev/null && break
    sleep 10
  done

  "${KUBECTL[@]}" -n bench-runner logs "job/$job" --tail=-1 2>/dev/null \
    | awk '/@@SUMMARY_BEGIN@@/{f=1;next} /@@SUMMARY_END@@/{f=0} f' > "$OUT/$c.k6.json"
  "${KUBECTL[@]}" -n bench-runner delete job "$job" --ignore-not-found >/dev/null 2>&1
  sleep 20
  "${KUBECTL[@]}" -n bench-sink exec deploy/trace-capture -c reader -- \
    cat /capture/spans.json > "$OUT/$c.spans.json" 2>/dev/null

  python3 - "$OUT" "$c" "$(spans_of "$c")" <<'PY'
import json, os, sys
from collections import Counter
out, cell, expect = sys.argv[1], sys.argv[2], float(sys.argv[3])
try:
    k = json.load(open(f'{out}/{cell}.k6.json'))['overall']
except Exception:
    print(f'{cell:<8} no k6 summary'); raise SystemExit
sp = f'{out}/{cell}.spans.json'
if not (os.path.exists(sp) and os.path.getsize(sp)):
    print(f'{cell:<8} requests={k["requests"]:<7} no spans captured'); raise SystemExit
traces, kinds, names, per_trace = set(), Counter(), Counter(), Counter()
spans = 0
for line in open(sp):
    line = line.strip()
    if not line:
        continue
    try:
        doc = json.loads(line)
    except json.JSONDecodeError:
        continue
    for rs in doc.get('resourceSpans', []):
        for ss in rs.get('scopeSpans', []):
            for s in ss.get('spans', []):
                spans += 1
                traces.add(s.get('traceId'))
                per_trace[s.get('traceId')] += 1
                kinds[s.get('kind')] += 1
                names[s.get('name')] += 1
req = k['requests']
shape = Counter(per_trace.values())
print(f'{cell:<8} requests={req:<7} traces={len(traces):<6} spans={spans:<7} '
      f'kept={len(traces) / req * 100:5.1f}%  spans/trace={spans / max(len(traces), 1):.2f}  '
      f'spans/request={spans / req:.2f} (design {expect:.0f})')
print(f'         kinds: ' + ', '.join(f'{ {1: "INTERNAL", 2: "SERVER", 3: "CLIENT", 4: "PRODUCER", 5: "CONSUMER"}.get(k, k)}={v}' for k, v in sorted(kinds.items())))
print(f'         spans per trace: ' + ', '.join(f'{n} spans x {c}' for n, c in sorted(shape.items())))
print(f'         top names: ' + ', '.join(f'{n} ({c})' for n, c in names.most_common(10)))
PY
done
echo "spans: $OUT"
