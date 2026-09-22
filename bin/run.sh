#!/usr/bin/env bash
# Bracketed overhead run: every cell in lockstep, one arm at a time.
#
# Each guard below replaces a defect that silently corrupted an earlier run:
#
#   ConfigMap    the k6 script is re-uploaded and verified byte-for-byte before
#                every run. The cluster had been holding a script with no
#                constant-arrival-rate executor, so every "fixed 100 req/s" arm
#                actually ran at concurrency 1 and nothing said so.
#   slots        results land in NN-<arm> directories. Both baselines used to
#                write off.k6.json and the first one was destroyed.
#   cgroup       CPU comes from the container's own cpu.stat plus its own clock,
#                not from cAdvisor divided by a laptop wall clock.
#   gate         an arm with client timeouts, errors, dropped iterations, CPU
#                throttling, a CFS quota, a restarted pod, unsettled halves,
#                the wrong InstrumentationConfig or the wrong span count is
#                rejected instead of averaged in.
#   job status   a failed k6 Job is a failure, not a completed arm.
#   reset        /admin/reset must answer 200 after every restart, or the arm
#                would start from a database in an unknown state.
#
# Arms default to off 100 off 25 off: every instrumented arm is bracketed by an
# uninstrumented arm on both sides, so only drift inside ~20 minutes can reach
# the result. Nothing is ever compared across cells.
#
#   CELLS="s m" ARMS="off 100 off" WARM=3m DUR=3m bin/run.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/bin/lib/cells.sh"
RESULTS="${RESULTS:-$ROOT/results/run-$(date +%m%d-%H%M%S)}"
mkdir -p "$RESULTS"

KUBECTL=(kubectl)
[ -n "${KUBE_CONTEXT:-}" ] && KUBECTL+=(--context "$KUBE_CONTEXT")
CELLS="${CELLS:-$(cells_all)}"
ARMS="${ARMS:-off 100 off 25 off}"
# 20 minutes because C2 compiles on invocation counts: the 20 req/s cell needs
# ~20k requests before its per-request path is fully compiled.
WARM="${WARM:-20m}"
DUR="${DUR:-8m}"
# RESTART=each (default): every arm is a fresh JVM (restart + warm-up).
# RESTART=once (what the validation battery uses): the JVM is restarted and warmed only before the
# first arm; later arms attach/reconfigure the agent on the SAME running JVM
# (Odigos attaches to a running JVM without a pod restart) and re-warm for
# WARM. Same-JVM arms remove the 2-15% between-JVM code-quality scatter that
# otherwise limits the resolution on 12-60 ms requests. WARM_FIRST is the
# warm-up before the first arm (default WARM).
RESTART="${RESTART:-each}"
WARM_FIRST="${WARM_FIRST:-$WARM}"
WARM_RAMP="${WARM_RAMP:-60s}"
START_MARGIN="${START_MARGIN:-10}"   # seconds after the warm boundary before the window opens
END_MARGIN="${END_MARGIN:-30}"       # seconds before k6 stops at which the window closes
STATEFUL="${STATEFUL:-postgres redis}"
APP="${APP:-bucket-app}"
P99_S="${P99_S:-0.5}"
TOOLS="${TOOLS:-bench-tools}"
LIB="$ROOT/bin/lib"

for c in $CELLS; do require_cell "$c" || exit 1; done

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$RESULTS/run.log"; }

dur_seconds() {
  case "$1" in
    *m) echo $(( ${1%m} * 60 ));;
    *s) echo "${1%s}";;
    *)  echo "$1";;
  esac
}

WARM_S=$(dur_seconds "$WARM")
WARM_FIRST_S=$(dur_seconds "$WARM_FIRST")
DUR_S=$(dur_seconds "$DUR")

ns_of()  { echo "cell-$1"; }
url_of() { echo "http://$APP.$(ns_of "$1").svc.cluster.local:8080"; }

# The knob the pod actually runs with: the app's own /admin/config
# (effective.cpu_units), else the Deployment env, else the preset value.
live_units_of() {
  local u
  u=$(tools_curl "$(url_of "$1")/admin/config" \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["effective"]["cpu_units"])' 2>/dev/null)
  if [ -z "$u" ]; then
    u=$("${KUBECTL[@]}" -n "$(ns_of "$1")" get deploy "$APP" \
        -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="BENCH_CPU_UNITS")].value}' 2>/dev/null)
  fi
  echo "${u:-$(units_of "$1")}"
}

# The script the cluster runs must be the script in this repo. Verified, not assumed.
sync_script() {
  "${KUBECTL[@]}" -n bench-runner create configmap k6-script \
    --from-file=load.js="$ROOT/k6/load.js" \
    --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f - >/dev/null
  "${KUBECTL[@]}" -n bench-runner get cm k6-script \
    -o "jsonpath={.data.load\\.js}" > "$RESULTS/k6-script.live.js"
  if ! diff -q "$RESULTS/k6-script.live.js" "$ROOT/k6/load.js" >/dev/null; then
    log "FATAL: k6 ConfigMap does not match local script after apply"
    exit 1
  fi
  log "k6 script synced and verified ($(wc -c < "$ROOT/k6/load.js" | tr -d ' ') bytes)"
}

set_sampling() {
  "${KUBECTL[@]}" -n odigos-system delete samplings.odigos.io --all >/dev/null 2>&1
  if [ "$1" = "off" ]; then
    for c in $CELLS; do
      "${KUBECTL[@]}" -n "$(ns_of "$c")" delete sources.odigos.io bench-workload-src --ignore-not-found >/dev/null 2>&1
    done
    return
  fi
  for c in $CELLS; do
    CELL="$c" envsubst < "$ROOT/k8s/odigos/source.tpl.yaml" | "${KUBECTL[@]}" apply -f - >/dev/null
  done
  if [ "$1" != "100" ]; then
    sed "s/percentageAtMost: PCT/percentageAtMost: $1/" "$ROOT/k8s/odigos/sampling.tpl.yaml" \
      | "${KUBECTL[@]}" apply -f - >/dev/null
  fi
}

# The InstrumentationConfig is what the agent actually runs with; wait until it
# reflects the arm just asked for before recording it (attach/reconfigure on a
# live JVM takes seconds; a restart takes longer).
wait_ic() {
  local c="$1" arm="$2" i txt
  for i in $(seq 1 30); do
    txt=$("${KUBECTL[@]}" -n "$(ns_of "$c")" get instrumentationconfigs -o yaml 2>/dev/null)
    case "$arm" in
      off) echo "$txt" | grep -q 'kind: InstrumentationConfig' || return 0;;
      100) echo "$txt" | grep -q 'agentEnabled: true' && ! echo "$txt" | grep -q 'percentageAtMost' && return 0;;
      *)   echo "$txt" | grep -q "percentageAtMost: $arm" && return 0;;
    esac
    sleep 3
  done
  log "  WARN: $c InstrumentationConfig did not reflect arm $arm within 90 s"
  return 0
}

reset_and_restart() {
  for c in $CELLS; do
    for d in $STATEFUL; do
      "${KUBECTL[@]}" -n "$(ns_of "$c")" rollout restart "deploy/$d" >/dev/null 2>&1
    done
  done
  for c in $CELLS; do
    for d in $STATEFUL; do
      "${KUBECTL[@]}" -n "$(ns_of "$c")" rollout status "deploy/$d" --timeout=8m >/dev/null 2>&1
    done
  done
  for c in $CELLS; do
    "${KUBECTL[@]}" -n "$(ns_of "$c")" rollout restart "deploy/$APP" >/dev/null
  done
  for c in $CELLS; do
    "${KUBECTL[@]}" -n "$(ns_of "$c")" rollout status "deploy/$APP" --timeout=12m >/dev/null
  done
}

tools_curl() {
  "${KUBECTL[@]}" -n bench-runner exec "$TOOLS" -- curl -s --max-time 120 "$@" 2>/dev/null
}

# Timestamp and scrape from the same pod, so the window is the pod's own clock.
# The clock is the node's uptime in ns: the tools and probe images are busybox,
# whose date has no %N. Each URL's body follows a "@@ <port>" line so one file
# can hold several.
tools_scrape() {
  local out="$1"; shift
  local cmd='echo "TS=$(awk "{printf \"%.0f\", \$1 * 1000000000}" /proc/uptime)"' u port
  for u in "$@"; do
    port="${u##*:}"; port="${port%%/*}"
    cmd="$cmd; echo '@@ $port'; curl -s --max-time 20 '$u'; echo"
  done
  "${KUBECTL[@]}" -n bench-runner exec "$TOOLS" -- sh -c "$cmd" > "$out" 2>/dev/null
}

admin_reset() {
  local code
  code=$(tools_curl -o /dev/null -w '%{http_code}' -X POST "$(url_of "$1")/admin/reset")
  echo "${code:-000}"
}

# Poll /healthz (200 only when Postgres, Redis and echo answer) instead of a
# fixed sleep after the restart.
wait_healthy() {
  local c="$1" deadline=$(( $(date +%s) + 300 )) code
  while [ "$(date +%s)" -lt "$deadline" ]; do
    code=$(tools_curl -o /dev/null -w '%{http_code}' "$(url_of "$c")/healthz")
    [ "$code" = "200" ] && return 0
    sleep 5
  done
  log "  FATAL: $c not healthy after restart (last /healthz $code)"
  return 1
}

iso_to_epoch() {
  python3 -c 'import sys,datetime as d
s=sys.stdin.read().strip()
print(int(d.datetime.strptime(s,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=d.timezone.utc).timestamp()) if s else "")'
}
# Epoch seconds when a job's k6 container started running, or empty.
k6_started_at() {
  "${KUBECTL[@]}" -n bench-runner get pods -l "job-name=$1" \
    -o jsonpath='{.items[0].status.containerStatuses[0].state.running.startedAt}' 2>/dev/null | iso_to_epoch
}
k6_completed_at() {
  "${KUBECTL[@]}" -n bench-runner get job "$1" -o jsonpath='{.status.completionTime}' 2>/dev/null | iso_to_epoch
}
job_failed() {
  local f
  f=$("${KUBECTL[@]}" -n bench-runner get job "$1" -o jsonpath='{.status.failed}' 2>/dev/null)
  [ "${f:-0}" -ge 1 ] 2>/dev/null
}
# Sleep until an epoch second, failing if any run job dies meanwhile: a k6 that
# died during the warm phase leaves a JVM that was never warmed.
sleep_until() {
  local target="$1" what="$2" now left c
  while now=$(date +%s); [ "$now" -lt "$target" ]; do
    for c in $CELLS; do
      if job_failed "k6-run-$c"; then
        log "  FATAL: k6-run-$c failed before the $what"
        return 1
      fi
    done
    left=$(( target - now ))
    sleep $(( left < 15 ? left : 15 ))
  done
  return 0
}

launch() {
  local c="$1" dur="$2" tag="$3" mode="$4" warm_s="${5:-0}"
  local job="k6-${tag}-${c}"
  "${KUBECTL[@]}" -n bench-runner delete job "$job" --ignore-not-found >/dev/null 2>&1
  JOB_NAME="$job" \
  BASE_URL="$(url_of "$c")" \
  K6_MODE="$mode" K6_DURATION="$dur" K6_WARM_RAMP="$WARM_RAMP" K6_WARM_S="$warm_s" \
  K6_RATE="$(rate_of "$c")" K6_P99_S="$P99_S" K6_PRE_VUS="" K6_MAX_VUS="" \
  K6_EXPECT_UNITS="$(live_units_of "$c")" K6_EXPECT_JDBC="$(jdbc_of "$c")" \
  K6_EXPECT_REDIS="$(redis_of "$c")" K6_EXPECT_HTTP="$(http_of "$c")" \
  K6_CPU_REQ="$(k6cpu_of "$c")" K6_CPU_LIM="$(k6cpu_of "$c")" \
    envsubst < "$ROOT/k8s/runner/k6-job.tpl.yaml" | "${KUBECTL[@]}" apply -f - >/dev/null
  # Confirm the job exists. A transient API error here used to disappear into
  # /dev/null, and the arm then ran on for 90 minutes waiting for something that
  # was never created.
  local out
  out=$("${KUBECTL[@]}" -n bench-runner get job "$job" 2>&1)
  if echo "$out" | grep -q 'NotFound'; then
    log "  FATAL: job $job was not created"
    return 1
  fi
  return 0
}

# Returns 1 if any job failed, so a failed arm cannot masquerade as a finished one.
wait_all() {
  local tag="$1" total rc=0
  total=$(echo "$CELLS" | wc -w | tr -d ' ')
  local deadline=$(( $(date +%s) + 5400 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    local done_n=0
    for c in $CELLS; do
      local s f probe
      probe=$("${KUBECTL[@]}" -n bench-runner get job "k6-${tag}-${c}" 2>&1)
      if echo "$probe" | grep -q 'NotFound'; then
        log "  FATAL: job k6-${tag}-${c} does not exist"
        return 1
      fi
      # Anything else - Unauthorized, timeout, TLS - is the API being briefly
      # unreachable. Expired credentials once turned that into a dead run.
      if echo "$probe" | grep -qiE 'Unauthorized|unable to connect|timed out|refused|TLS'; then
        log "  API unreachable, retrying: $(echo "$probe" | head -1)"
        sleep 20
        continue
      fi
      s=$("${KUBECTL[@]}" -n bench-runner get job "k6-${tag}-${c}" -o jsonpath='{.status.succeeded}' 2>/dev/null)
      f=$("${KUBECTL[@]}" -n bench-runner get job "k6-${tag}-${c}" -o jsonpath='{.status.failed}' 2>/dev/null)
      [ "${s:-0}" -ge 1 ] 2>/dev/null && done_n=$(( done_n + 1 ))
      if [ "${f:-0}" -ge 1 ] 2>/dev/null; then
        log "  JOB FAILED: k6-${tag}-${c}"
        done_n=$(( done_n + 1 )); rc=1
      fi
    done
    [ "$done_n" -ge "$total" ] && return $rc
    sleep 20
  done
  log "  TIMEOUT waiting for $tag jobs"
  return 1
}

# Container-local clock and CPU counter in one read, so they cannot disagree.
# cpu.max and the effective cpuset are the exclusive-core evidence the gate needs.
snap() {
  local ns="$1" pod="$2" out="$3"
  "${KUBECTL[@]}" -n "$ns" exec "$pod" -c app -- \
    sh -c 'echo TS=$(date +%s%N); cat /sys/fs/cgroup/cpu.stat;
           echo mem_current $(cat /sys/fs/cgroup/memory.current);
           echo mem_peak $(cat /sys/fs/cgroup/memory.peak 2>/dev/null);
           echo cpu_max $(cat /sys/fs/cgroup/cpu.max);
           echo cpuset $(cat /sys/fs/cgroup/cpuset.cpus.effective)' 2>/dev/null > "$out"
}

probe_pod_of() {
  "${KUBECTL[@]}" -n bench-runner get pods -l app=node-probe \
    --field-selector "spec.nodeName=$1" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null
}

odigos_pods_on() {
  "${KUBECTL[@]}" -n odigos-system get pods --field-selector "spec.nodeName=$1" \
    -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.uid}{" "}{end}' 2>/dev/null
}

# Root cgroup, every Odigos pod cgroup on the app node and each of their
# container scopes, from the probe pod. The odiglet pod carries the agent,
# the node collector (data-collection) and the device plugin as three
# containers of one pod, so the split has to be per container:
#   kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod<uid>.slice/cri-containerd-<id>.scope
# (Guaranteed pods sit one level up as kubepods-pod<uid>.slice; -maxdepth 2
# from kubepods.slice reaches both). The container name -> id map is appended
# from the pod status so the parser can name each scope.
node_snap() {
  local node="$1" out="$2" probe pods p name
  probe=$(probe_pod_of "$node")
  [ -n "$probe" ] || { : > "$out"; return; }
  pods=$(odigos_pods_on "$node")
  # shellcheck disable=SC2086
  "${KUBECTL[@]}" -n bench-runner exec "$probe" -- sh -c '
    echo "TS=$(awk "{printf \"%.0f\", \$1 * 1000000000}" /proc/uptime)"
    echo "@@ root"; cat /host/sys/fs/cgroup/cpu.stat
    for s in "$@"; do
      n=${s%%=*}; u=$(echo "${s#*=}" | tr - _)
      echo "@@ pod $n"
      d=$(find /host/sys/fs/cgroup/kubepods.slice -maxdepth 2 -type d -name "*pod${u}.slice" | head -1)
      [ -n "$d" ] || continue
      cat "$d/cpu.stat"
      for sc in "$d"/cri-containerd-*.scope; do
        [ -d "$sc" ] || continue
        id=${sc##*/cri-containerd-}; id=${id%.scope}
        echo "@@ ctr $n $id"; cat "$sc/cpu.stat"
      done
    done' sh $pods > "$out" 2>/dev/null
  for p in $pods; do
    name=${p%%=*}
    "${KUBECTL[@]}" -n odigos-system get pod "$name" \
      -o jsonpath='{range .status.containerStatuses[*]}{.name}={.containerID}{"\n"}{end}' 2>/dev/null \
      | sed -e 's|containerd://||' -e "s|^|@@ ctrmap $name |" >> "$out"
  done
}

pod_of() {
  "${KUBECTL[@]}" -n "$(ns_of "$1")" get pods -l "app=$APP" \
    -o jsonpath='{.items[?(@.status.phase=="Running")].metadata.name}' | awk '{print $1}'
}
restarts_of() {
  "${KUBECTL[@]}" -n "$(ns_of "$1")" get pod "$2" -o jsonpath='{.status.containerStatuses[0].restartCount}' 2>/dev/null
}

SINK_URLS="http://otlp-sink.bench-sink.svc.cluster.local:8888/metrics http://otlp-sink.bench-sink.svc.cluster.local:8889/metrics"

"${KUBECTL[@]}" -n bench-runner get pod "$TOOLS" >/dev/null 2>&1 || {
  log "FATAL: tools pod $TOOLS missing in bench-runner (kubectl apply -f k8s/runner/namespace.yaml)"; exit 1; }
sync_script
log "cells: $CELLS"
for c in $CELLS; do
  log "  $c preset=$(preset_of "$c") rate=$(rate_of "$c") k6cpu=$(k6cpu_of "$c") units=$(live_units_of "$c") spans=$(spans_of "$c")"
done
log "arms: $ARMS  restart=$RESTART warm_first=$WARM_FIRST warm=$WARM (ramp $WARM_RAMP) measure=$DUR"

idx=0
for arm in $ARMS; do
  idx=$(( idx + 1 ))
  slot="$(printf '%02d' $idx)-$arm"
  log "=== arm $slot ==="
  set_sampling "$arm"
  if [ "$RESTART" = "each" ] || [ "$idx" -eq 1 ]; then
    reset_and_restart
    arm_warm_s=$WARM_FIRST_S
  else
    arm_warm_s=$WARM_S
  fi
  [ "$idx" -eq 1 ] && arm_warm_s=$WARM_FIRST_S
  for c in $CELLS; do wait_healthy "$c" || exit 1; done
  for c in $CELLS; do wait_ic "$c" "$arm"; done

  RD="$RESULTS/$slot"; mkdir -p "$RD"
  for c in $CELLS; do
    code=$(admin_reset "$c")
    echo "$code" > "$RD/$c.reset.http"
    if [ "$code" != "200" ]; then
      log "FATAL: /admin/reset on $c returned $code - database not in a known state, arm aborted"
      exit 1
    fi
  done
  for c in $CELLS; do
    "${KUBECTL[@]}" -n "$(ns_of "$c")" get instrumentationconfigs -o yaml > "$RD/$c.ic.yaml" 2>&1
    # Effective knobs, JVM arguments and processor count as the app sees them.
    tools_curl "$(url_of "$c")/admin/config" > "$RD/$c.config.json"
  done

  # One continuous k6 job per cell: ramp, hold for the warm phase, then the
  # measure phase at the same rate. A separate warm job, a pause and a fresh
  # job gave the measure window a cold VU pool and cold connections at its
  # start, visible as a CPU/req bump and disagreeing half-windows. The warm
  # phase is equal for every arm, so no arm is measured JIT-cold.
  for c in $CELLS; do
    launch "$c" "$DUR" run warmrun "$arm_warm_s" || { log "FATAL: could not start k6 for $c"; exit 1; }
  done
  earliest=0; latest=0
  for c in $CELLS; do
    started=""; deadline=$(( $(date +%s) + 300 ))
    while [ -z "$started" ]; do
      started=$(k6_started_at "k6-run-$c")
      [ -n "$started" ] && break
      if job_failed "k6-run-$c" || [ "$(date +%s)" -gt "$deadline" ]; then
        log "FATAL: k6-run-$c did not start"; exit 1
      fi
      sleep 5
    done
    echo "$started" > "$RD/$c.k6.started"
    [ "$earliest" -eq 0 ] || [ "$started" -lt "$earliest" ] && earliest=$started
    [ "$started" -gt "$latest" ] && latest=$started
  done
  log "  k6 running in every cell; warm phase ${arm_warm_s}s, measure ${DUR_S}s"

  # The cgroup window lies strictly inside the measure phase: it opens
  # START_MARGIN after the last cell's warm boundary and closes END_MARGIN
  # before the first cell's k6 stops. Requests in the window are then the
  # achieved rate times the window, so no idle tail and no partial second is
  # ever charged to a request.
  sleep_until $(( latest + arm_warm_s + START_MARGIN )) "warm boundary" || exit 1
  for c in $CELLS; do
    ns=$(ns_of "$c")
    p=$(pod_of "$c")
    echo "$p" > "$RD/$c.pod"
    "${KUBECTL[@]}" -n "$ns" get pod "$p" -o jsonpath='{.spec.nodeName}' > "$RD/$c.node"
    "${KUBECTL[@]}" -n "$ns" get pod "$p" -o jsonpath='{.status.qosClass}' > "$RD/$c.qos"
    "${KUBECTL[@]}" -n "$ns" get pod "$p" -o jsonpath='{.spec.containers[0].image}' > "$RD/$c.image"
    restarts_of "$c" "$p" > "$RD/$c.restarts.start"
    snap "$ns" "$p" "$RD/$c.cg.start"
    node_snap "$(cat "$RD/$c.node")" "$RD/$c.probe.start"
    tools_scrape "$RD/$c.jvm.start" "$(url_of "$c")/actuator/prometheus"
    date +%s > "$RD/$c.sink.start.epoch"
    tools_scrape "$RD/$c.sink.start" $SINK_URLS
  done

  # Snapshot halfway through, so every arm yields two independent CPU
  # measurements of the same steady state. If the halves disagree the arm was
  # still settling - JIT compiler threads burn CPU for minutes after a restart,
  # and that CPU lands in the window and is charged to the requests served.
  # Two halves that agree is the evidence that the number means anything.
  window_end=$(( earliest + arm_warm_s + DUR_S - END_MARGIN ))
  sleep_until $(( ( latest + arm_warm_s + START_MARGIN + window_end ) / 2 )) "mid-window" || exit 1
  for c in $CELLS; do
    snap "$(ns_of "$c")" "$(cat "$RD/$c.pod")" "$RD/$c.cg.mid"
  done

  sleep_until "$window_end" "window end" || exit 1
  for c in $CELLS; do
    ns=$(ns_of "$c"); p=$(cat "$RD/$c.pod")
    snap "$ns" "$p" "$RD/$c.cg.end"
    node_snap "$(cat "$RD/$c.node")" "$RD/$c.probe.end"
    tools_scrape "$RD/$c.jvm.end" "$(url_of "$c")/actuator/prometheus"
    restarts_of "$c" "$p" > "$RD/$c.restarts.end"
    pod_of "$c" > "$RD/$c.pod.end"
  done

  wait_all run; run_rc=$?

  # The gateway batches and the spanmetrics connector flushes every 15 s; the
  # last requests' spans land after k6 has exited. The sink window therefore
  # runs from the start scrape to the job's completion, and its request count
  # is the achieved rate times that span of time.
  sleep 30
  for c in $CELLS; do
    k6_completed_at "k6-run-$c" > "$RD/$c.k6.completed"
    tools_scrape "$RD/$c.sink.end" $SINK_URLS
  done

  for c in $CELLS; do
    ns=$(ns_of "$c"); p=$(cat "$RD/$c.pod")
    "${KUBECTL[@]}" -n bench-runner logs "job/k6-run-${c}" --tail=-1 2>/dev/null \
      | awk '/@@SUMMARY_BEGIN@@/{f=1;next} /@@SUMMARY_END@@/{f=0} f' > "$RD/$c.k6.json"
    "${KUBECTL[@]}" -n bench-runner delete job "k6-run-${c}" --ignore-not-found >/dev/null 2>&1

    # Achieved rate of the measure phase (measure requests / DURATION); the
    # gate requires it within 1% of the offered rate, so every window's request
    # count is tps x window to that precision.
    tps=$(python3 -c "import json;print(json.load(open('$RD/$c.k6.json'))['overall']['tps'])" 2>/dev/null || echo 0)
    python3 "$LIB/cgroup_delta.py" "$RD/$c.cg.start" "$RD/$c.cg.end" "tps=$tps" \
      > "$RD/$c.cpu.json" 2>/dev/null
    if [ -s "$RD/$c.cg.mid" ]; then
      python3 "$LIB/cgroup_delta.py" "$RD/$c.cg.start" "$RD/$c.cg.mid" "tps=$tps" \
        > "$RD/$c.cpu.h1.json" 2>/dev/null
      python3 "$LIB/cgroup_delta.py" "$RD/$c.cg.mid" "$RD/$c.cg.end" "tps=$tps" \
        > "$RD/$c.cpu.h2.json" 2>/dev/null
    fi
    sink_reqs=$(python3 -c "print(int(round($tps * ($(cat "$RD/$c.k6.completed" 2>/dev/null || echo 0) - $(cat "$RD/$c.sink.start.epoch" 2>/dev/null || echo 0)))))" 2>/dev/null || echo 0)
    python3 "$LIB/sink_delta.py" "$RD/$c.sink.start" "$RD/$c.sink.end" \
      --service "bucket-app-$c" --requests "$sink_reqs" --expect-spans "$(spans_of "$c")" \
      > "$RD/$c.sink.json" 2>/dev/null
    python3 "$LIB/jvm_delta.py" "$RD/$c.jvm.start" "$RD/$c.jvm.end" > "$RD/$c.jvm.json" 2>/dev/null
    spr=$(python3 -c "import json;print(json.load(open('$RD/$c.sink.json')).get('spans_per_req') or 0)" 2>/dev/null || echo 0)
    python3 "$LIB/node_delta.py" "$RD/$c.probe.start" "$RD/$c.probe.end" \
      --tps "$tps" --spans-per-req "$spr" > "$RD/$c.probe.json" 2>/dev/null

    printf '{"cell":"%s","preset":"%s","arm":"%s","slot":"%s","pod":"%s","pod_end":"%s","node":"%s","qos":"%s","restarts_start":%s,"restarts_end":%s,"arrival_rate":%s,"duration":"%s","warm":"%s","restart":"%s","job_rc":%s,"reset_http":%s,"cpu_units":%s,"expect_spans":%s,"k6_cpu":%s,"image":"%s"}\n' \
      "$c" "$(preset_of "$c")" "$arm" "$slot" "$p" "$(cat "$RD/$c.pod.end")" "$(cat "$RD/$c.node")" "$(cat "$RD/$c.qos")" \
      "$(cat "$RD/$c.restarts.start" 2>/dev/null || echo null)" "$(cat "$RD/$c.restarts.end" 2>/dev/null || echo null)" \
      "$(rate_of "$c")" "$DUR" "${arm_warm_s}s" "$RESTART" "$run_rc" "$(cat "$RD/$c.reset.http")" "$(live_units_of "$c")" "$(spans_of "$c")" "$(k6cpu_of "$c")" "$(cat "$RD/$c.image" 2>/dev/null)" \
      > "$RD/$c.meta.json"

    if python3 "$LIB/gate.py" "$RD/$c.k6.json" "$RD/$c.cpu.json" --arm "$arm" \
         --meta "$RD/$c.meta.json" --h1 "$RD/$c.cpu.h1.json" --h2 "$RD/$c.cpu.h2.json" \
         --sink "$RD/$c.sink.json" --jvm "$RD/$c.jvm.json" --ic "$RD/$c.ic.yaml" \
         --expect-spans "$(spans_of "$c")" > "$RD/$c.gate.json" 2>&1; then
      log "  $c  ACCEPTED"
    else
      log "  $c  REJECTED: $(python3 -c "import json;print('; '.join(json.load(open('$RD/$c.gate.json'))['reasons']))" 2>/dev/null)"
    fi
  done
  log "  arm $slot done (job_rc=$run_rc)"
done

log "run complete: $RESULTS"
echo "$RESULTS"
