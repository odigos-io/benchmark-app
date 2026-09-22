#!/usr/bin/env bash
# Size each bucket's CPU knob on the node it will be measured on.
#
# Per cell: (1) a calibration Job on the cell's app node prints µs/unit with
# one and two threads; (2) three uninstrumented arms at 0.5x, 1x and 1.5x of
# the preset units give measured CPU ms/req under load; (3) a least-squares
# line ms/req = a + b*units yields the units that land on the cell's target.
# The loaded fit, not the idle calibration, is what sizes the bucket: a JVM
# serving requests pays for GC, the Tomcat thread, JDBC and Redis I/O that
# the bare primitive never sees.
#
#   CELLS="m l" bin/calibrate.sh
#
# Nothing is applied: the script prints `kubectl set env` lines per cell.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/bin/lib/cells.sh"
KUBECTL=(kubectl)
[ -n "${KUBE_CONTEXT:-}" ] && KUBECTL+=(--context "$KUBE_CONTEXT")
CELLS="${CELLS:-$(cells_all)}"
WARM="${WARM:-12m}"
DUR="${DUR:-6m}"
APP="${APP:-bucket-app}"
FACTORS="${FACTORS:-0.5 1 1.5}"
OUT="${OUT:-$ROOT/results/calibrate-$(date +%m%d-%H%M%S)}"
mkdir -p "$OUT"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$OUT/calibrate.log"; }

for c in $CELLS; do require_cell "$c" || exit 1; done

app_image_of() {
  "${KUBECTL[@]}" -n "cell-$1" get deploy "$APP" -o jsonpath='{.spec.template.spec.containers[0].image}'
}
java_opts_of() {
  "${KUBECTL[@]}" -n "cell-$1" get deploy "$APP" \
    -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="JAVA_TOOL_OPTIONS")].value}'
}
set_units() {
  if [ -z "$2" ]; then
    "${KUBECTL[@]}" -n "cell-$1" set env "deploy/$APP" BENCH_CPU_UNITS- >/dev/null
  else
    "${KUBECTL[@]}" -n "cell-$1" set env "deploy/$APP" "BENCH_CPU_UNITS=$2" >/dev/null
  fi
}

for c in $CELLS; do
  log "=== cell $c (preset $(preset_of "$c"), target $(target_ms_of "$c") ms/req) ==="
  original=$("${KUBECTL[@]}" -n "cell-$c" get deploy "$APP" \
    -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="BENCH_CPU_UNITS")].value}')

  # The app pod holds the only exclusive core on that node; the calibration Job
  # needs it, so the pod is scaled away for the duration of the Job.
  "${KUBECTL[@]}" -n "cell-$c" scale "deploy/$APP" --replicas=0 >/dev/null
  "${KUBECTL[@]}" -n "cell-$c" wait --for=delete pod -l "app=$APP" --timeout=3m >/dev/null 2>&1
  job="bucket-calibrate-$c"
  "${KUBECTL[@]}" -n "cell-$c" delete job "$job" --ignore-not-found >/dev/null 2>&1
  JOB_NAME="$job" CELL="$c" PRESET="$(preset_of "$c")" APP_IMAGE="$(app_image_of "$c")" \
  JAVA_TOOL_OPTIONS="$(java_opts_of "$c")" \
    envsubst < "$ROOT/k8s/runner/calibrate-job.tpl.yaml" | "${KUBECTL[@]}" apply -f - >/dev/null
  "${KUBECTL[@]}" -n "cell-$c" wait --for=condition=complete "job/$job" --timeout=15m >/dev/null 2>&1
  "${KUBECTL[@]}" -n "cell-$c" logs "job/$job" --tail=-1 > "$OUT/$c.calibrate.log" 2>&1
  "${KUBECTL[@]}" -n "cell-$c" delete job "$job" --ignore-not-found >/dev/null 2>&1
  "${KUBECTL[@]}" -n "cell-$c" scale "deploy/$APP" --replicas=1 >/dev/null
  log "calibration job output:"
  sed 's/^/    /' "$OUT/$c.calibrate.log" | tail -20 | tee -a "$OUT/calibrate.log"

  base=$(units_of "$c")
  points=""
  for f in $FACTORS; do
    u=$(python3 -c "print(max(1, round($base * $f)))")
    set_units "$c" "$u"
    log "  off arm at cpu_units=$u"
    rd=$(RESULTS="$OUT/$c-u$u" CELLS="$c" ARMS="off" WARM="$WARM" DUR="$DUR" "$ROOT/bin/run.sh" | tail -1)
    ms=$(python3 -c "
import json,sys
try:
    g=json.load(open('$rd/01-off/$c.gate.json')); c=json.load(open('$rd/01-off/$c.cpu.json'))
    print(c['cpu_ms_per_req'] if g.get('ok') else 'REJECTED')
except Exception as e:
    print('ERR')")
    log "    cpu_units=$u -> $ms ms/req"
    points="$points $u:$ms"
  done
  if [ -n "$original" ]; then set_units "$c" "$original"; else set_units "$c" ""; fi

  python3 - "$c" "$(target_ms_of "$c")" $points <<'PY' | tee -a "$OUT/calibrate.log"
import sys
cell, target = sys.argv[1], float(sys.argv[2])
pts = []
for p in sys.argv[3:]:
    u, ms = p.split(':')
    try:
        pts.append((float(u), float(ms)))
    except ValueError:
        print(f'  point units={u} unusable ({ms})')
if len(pts) < 2:
    print(f'  {cell}: fewer than two accepted points, cannot fit'); sys.exit(1)
n = len(pts)
sx = sum(u for u, _ in pts); sy = sum(m for _, m in pts)
sxx = sum(u * u for u, _ in pts); sxy = sum(u * m for u, m in pts)
b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
a = (sy - b * sx) / n
ss_res = sum((m - (a + b * u)) ** 2 for u, m in pts)
mean = sy / n
ss_tot = sum((m - mean) ** 2 for _, m in pts) or 1e-12
r2 = 1 - ss_res / ss_tot
units = (target - a) / b if b > 0 else float('nan')
print(f'  {cell}: ms/req = {a:.3f} + {b * 1000:.1f}us * units   (R^2 {r2:.4f}, {n} points)')
print(f'  {cell}: fixed cost {a:.3f} ms/req (I/O, Tomcat, GC); {b * 1000:.1f} us per unit under load')
print(f'  {cell}: units for {target} ms/req = {units:.0f}')
print(f'  kubectl -n cell-{cell} set env deploy/bucket-app BENCH_CPU_UNITS={units:.0f}')
if r2 < 0.99:
    print(f'  WARNING: R^2 {r2:.3f} < 0.99 - the knob is not linear on this node; rerun before trusting it')
PY
done
log "calibration written to $OUT"
