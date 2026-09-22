#!/usr/bin/env bash
# Drive the in-cluster orchestrator pod from a laptop that is allowed to sleep.
#
#   bin/incluster.sh sync                          copy cells.env, bin/, k6/, k8s/ to the pod's /work/kit
#   bin/incluster.sh run <name> [VAR=value ...]    start bin/run.sh detached; results in /work/results/<name>
#   bin/incluster.sh calibrate <name> [VAR=value ...]
#   bin/incluster.sh stop                                      kill every runner in the pod
#   bin/incluster.sh chain "<name>:VAR=v VAR=v" "<name2>:..."   several runs back to back, detached
#   bin/incluster.sh status [name]                 tail the run logs (all, or one)
#   bin/incluster.sh fetch <name>                  copy /work/results/<name> back to results/<name>
#   bin/incluster.sh shell                         interactive shell in the pod
#
# Every run is a nohup'd process inside the pod, so it survives this laptop
# sleeping, the terminal closing and the kubectl connection dropping. The pod
# itself must stay alive: it runs with strategy Recreate on the shared lane.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECTL=(kubectl)
[ -n "${KUBE_CONTEXT:-}" ] && KUBECTL+=(--context "$KUBE_CONTEXT")
NS=bench-runner
KIT=/work/kit

pod() {
  local p
  p=$("${KUBECTL[@]}" -n "$NS" get pods -l app=bench-orchestrator \
      --field-selector status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  if [ -z "$p" ]; then
    echo "no running bench-orchestrator pod in $NS (kubectl apply -f k8s/rendered/orchestrator.yaml)" >&2
    exit 1
  fi
  echo "$p"
}

pexec() { "${KUBECTL[@]}" -n "$NS" exec "$(pod)" -- "$@"; }

do_sync() {
  local p
  p=$(pod)
  pexec sh -c "rm -rf $KIT && mkdir -p $KIT /work/results /work/logs"
  for item in cells.env bin k6 k8s; do
    "${KUBECTL[@]}" -n "$NS" cp "$ROOT/$item" "$p:$KIT/$item"
  done
  pexec sh -c "chmod +x $KIT/bin/*.sh $KIT/bin/lib/*.py; cd $KIT && ls -1 bin k6 && wc -c < cells.env"
  # The copy is verified, not assumed: a stale script in the cluster once ran a
  # whole battery at the wrong concurrency without saying so.
  local here there
  here=$(cat "$ROOT/k6/load.js" "$ROOT/bin/run.sh" "$ROOT/cells.env" | shasum -a 256 | cut -c1-16)
  there=$(pexec sh -c "cat $KIT/k6/load.js $KIT/bin/run.sh $KIT/cells.env | sha256sum | cut -c1-16")
  if [ "$here" != "$there" ]; then
    echo "FATAL: kit in pod differs from local ($here vs $there)" >&2
    exit 1
  fi
  echo "synced and verified ($here)"
}

# Build the env prefix from VAR=value arguments; anything else is an error.
env_prefix() {
  local out="" a
  for a in "$@"; do
    case "$a" in
      *=*) out="$out $(printf '%q' "$a")";;
      *) echo "not VAR=value: $a" >&2; exit 1;;
    esac
  done
  echo "$out"
}

start_detached() {
  local script="$1" name="$2"; shift 2
  local envs
  envs=$(env_prefix "$@")
  pexec sh -c "mkdir -p /work/results /work/logs; cd $KIT && \
    nohup env RESULTS=/work/results/$name KUBE_CONTEXT= $envs bash bin/$script \
    > /work/logs/$name.log 2>&1 < /dev/null & echo started $name pid \$!"
}

# "ARMS=off 100 off WARM=30m" -> ARMS=off\ 100\ off WARM=30m : a word without a
# leading NAME= continues the previous value, then every assignment is quoted.
quote_assignments() {
  local out="" cur="" w
  for w in $1; do
    if [[ "$w" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      [ -n "$cur" ] && out="$out $(printf '%q' "$cur")"
      cur="$w"
    elif [ -n "$cur" ]; then
      cur="$cur $w"
    else
      echo "chain spec must start with VAR=value: $1" >&2; exit 1
    fi
  done
  [ -n "$cur" ] && out="$out $(printf '%q' "$cur")"
  echo "$out"
}

do_chain() {
  local id="chain-$(date +%m%d-%H%M%S)" lines="" spec name envs
  for spec in "$@"; do
    name="${spec%%:*}"; envs="${spec#*:}"
    [ "$name" != "$spec" ] || envs=""
    envs=$(quote_assignments "$envs")
    lines="$lines
echo \"[\$(date -u +%H:%M:%S)] === $name ===\"
env RESULTS=/work/results/$name KUBE_CONTEXT= $envs bash $KIT/bin/run.sh > /work/logs/$name.log 2>&1 || echo \"[\$(date -u +%H:%M:%S)] $name FAILED (rc \$?)\""
  done
  pexec sh -c "mkdir -p /work/results /work/logs; cat > /work/$id.sh <<'CHAIN'
#!/bin/bash
set -u
cd $KIT
$lines
echo \"[\$(date -u +%H:%M:%S)] chain complete\"
CHAIN
chmod +x /work/$id.sh
nohup bash /work/$id.sh > /work/logs/$id.log 2>&1 < /dev/null &
echo started $id pid \$!
cat /work/$id.sh"
}

# pkill -f <pattern> also matches the command line of the shell running it when
# the pattern is passed through `sh -c`: the killer kills itself and everything
# after it is skipped. A runner left behind that way keeps driving load and
# toggling Sources on the same cells as the next one, and both results are
# silently wrong. So: kill by pid from a listing that excludes this shell.
do_stop() {
  pexec sh -c '
    me=$$
    ps -o pid,args \
      | awk -v me="$me" "\$1 != me && (\$0 ~ /chain-[0-9]/ || \$0 ~ /bin\/run\.sh/ || \$0 ~ /bin\/calibrate\.sh/) {print \$1}" \
      | while read p; do kill -9 "$p" 2>/dev/null && echo "killed $p"; done
    sleep 2
    echo "-- remaining:"
    ps -o pid,args | grep -E "chain-[0-9]|bin/run\.sh|bin/calibrate\.sh" | grep -v grep || echo "  none"'
  $KUBECTL -n bench-runner delete jobs --all --ignore-not-found 2>&1 | tail -1
}

do_status() {
  if [ $# -ge 1 ]; then
    pexec sh -c "tail -n 40 /work/logs/$1.log"
  else
    pexec sh -c 'echo "-- processes"; ps -o pid,etime,args | grep -E "run.sh|calibrate.sh|chain-" | grep -v grep; \
      echo "-- logs"; for f in /work/logs/*.log; do echo "== $f"; tail -n 5 "$f"; done'
  fi
}

do_fetch() {
  local name="$1" p
  p=$(pod)
  mkdir -p "$ROOT/results"
  "${KUBECTL[@]}" -n "$NS" cp "$p:/work/results/$name" "$ROOT/results/$name"
  "${KUBECTL[@]}" -n "$NS" cp "$p:/work/logs/$name.log" "$ROOT/results/$name/orchestrator.log" 2>/dev/null || true
  echo "fetched results/$name ($(find "$ROOT/results/$name" -type f | wc -l | tr -d ' ') files)"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  sync)      do_sync;;
  run)       [ $# -ge 1 ] || { echo "run <name> [VAR=value ...]" >&2; exit 1; }
             name="$1"; shift; start_detached run.sh "$name" "$@";;
  calibrate) [ $# -ge 1 ] || { echo "calibrate <name> [VAR=value ...]" >&2; exit 1; }
             name="$1"; shift; start_detached calibrate.sh "$name" OUT="/work/results/$name" "$@";;
  stop)      do_stop;;
  chain)     [ $# -ge 1 ] || { echo "chain \"<name>:VAR=v ...\" ..." >&2; exit 1; }
             do_chain "$@";;
  status)    do_status "$@";;
  fetch)     [ $# -ge 1 ] || { echo "fetch <name>" >&2; exit 1; }
             do_fetch "$1";;
  shell)     "${KUBECTL[@]}" -n "$NS" exec -it "$(pod)" -- bash;;
  *)         sed -n '2,12p' "$0"; exit 1;;
esac
