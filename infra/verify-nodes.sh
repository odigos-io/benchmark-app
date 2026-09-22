#!/usr/bin/env bash
# Refuse to benchmark on nodes that are not what the method assumes.
#
# For every node labelled bench-cell it checks, from the kubelet's own configz
# and from the node's sysfs via the node-probe DaemonSet:
#
#   required  cpuManagerPolicy static, and cpu_manager_state present and static.
#             Without these the app pod runs under a CFS quota and every
#             measurement is invalid.
#   warned    full-pcpus-only not set. The pod still gets exclusive CPUs, but
#             they may be hyperthreads of two different cores. Usable; the
#             reference runs had it on.
#   optional  reservedSystemCPUs, checked only if EXPECT_RESERVED is set
#             (the reference EKS nodes used EXPECT_RESERVED=0,4).
#
# The hyperthread sibling layout is printed for the record, never enforced: it
# differs between VM families and providers. Exit status is nonzero only if a
# required check fails. Nothing is written.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBECTL=(kubectl)
[ -n "${KUBE_CONTEXT:-}" ] && KUBECTL+=(--context "$KUBE_CONTEXT")
EXPECT_RESERVED="${EXPECT_RESERVED:-}"
rc=0

"${KUBECTL[@]}" apply -f "$ROOT/k8s/runner/namespace.yaml" >/dev/null
"${KUBECTL[@]}" apply -f "$ROOT/k8s/runner/node-probe.yaml" >/dev/null
"${KUBECTL[@]}" -n bench-runner rollout status ds/node-probe --timeout=3m >/dev/null || {
  echo "node-probe DaemonSet did not become ready"; exit 1; }

nodes=$("${KUBECTL[@]}" get nodes -l bench-cell -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.labels.bench-cell}{"\n"}{end}')
if [ -z "$nodes" ]; then
  echo "no nodes carry label bench-cell"; exit 1
fi

while read -r node cell; do
  [ -n "$node" ] || continue
  probe=$("${KUBECTL[@]}" -n bench-runner get pods -l app=node-probe \
          --field-selector "spec.nodeName=$node" -o jsonpath='{.items[0].metadata.name}')
  configz=$("${KUBECTL[@]}" get --raw "/api/v1/nodes/$node/proxy/configz" 2>/dev/null)
  sysfs=$("${KUBECTL[@]}" -n bench-runner exec "$probe" -- sh -c '
    for c in /sys/devices/system/cpu/cpu[0-9]*; do
      echo "sib $(basename $c) $(cat $c/topology/thread_siblings_list)"
    done
    if [ -f /host/var/lib/kubelet/cpu_manager_state ]; then
      echo "state $(cat /host/var/lib/kubelet/cpu_manager_state)"
    else
      echo "state MISSING"
    fi
    echo "mhz $(awk "/cpu MHz/{print \$4; exit}" /host/proc/cpuinfo)"
    echo "model $(awk -F: "/model name/{print \$2; exit}" /host/proc/cpuinfo)"' 2>/dev/null)

  verdict=$(CONFIGZ="$configz" SYSFS="$sysfs" EXPECT_RESERVED="$EXPECT_RESERVED" python3 - <<'PY'
import json, os, re, sys
bad, warn = [], []
try:
    kc = json.loads(os.environ['CONFIGZ'])['kubeletconfig']
except Exception as e:
    print(f'FAIL configz unreadable: {e}'); sys.exit(1)
if kc.get('cpuManagerPolicy') != 'static':
    bad.append(f"cpuManagerPolicy={kc.get('cpuManagerPolicy')!r}")
opts = kc.get('cpuManagerPolicyOptions') or {}
if str(opts.get('full-pcpus-only', '')).lower() != 'true':
    warn.append(f"full-pcpus-only={opts.get('full-pcpus-only')!r} (exclusive CPUs may span two cores)")
if os.environ['EXPECT_RESERVED'] and kc.get('reservedSystemCPUs') != os.environ['EXPECT_RESERVED']:
    bad.append(f"reservedSystemCPUs={kc.get('reservedSystemCPUs')!r}, expected {os.environ['EXPECT_RESERVED']}")

sib, state, mhz, model = {}, None, None, ''
for ln in os.environ['SYSFS'].splitlines():
    p = ln.split(None, 2)
    if not p:
        continue
    if p[0] == 'sib' and len(p) == 3:
        sib[int(p[1][3:])] = p[2]
    elif p[0] == 'state':
        state = p[1] if len(p) == 2 else ' '.join(p[1:])
    elif p[0] == 'mhz' and len(p) > 1:
        mhz = p[1]
    elif p[0] == 'model':
        model = ' '.join(p[1:]).strip()
n = len(sib)
if n == 0:
    bad.append('no sysfs topology from probe')
pairs = sorted(set(sib.values()), key=lambda v: [int(x) for x in re.split(r'[,-]', v) if x.isdigit()])
if not state or state == 'MISSING':
    bad.append('cpu_manager_state missing')
else:
    try:
        st = json.loads(state)
        if st.get('policyName') != 'static':
            bad.append(f"cpu_manager_state policyName={st.get('policyName')!r}")
        if st.get('defaultCpuSet') is not None:
            print(f'  shared pool: {st.get("defaultCpuSet")}  exclusive: {st.get("entries") or {}}')
    except Exception as e:
        bad.append(f'cpu_manager_state unparsable: {e}')
print(f'  cpus={n} model={model} mhz={mhz}')
print(f"  reservedSystemCPUs={kc.get('reservedSystemCPUs')!r}  sibling sets: {' '.join(pairs)}")
for w in warn:
    print('  WARN ' + w)
if bad:
    print('  FAIL ' + '; '.join(bad)); sys.exit(1)
print('  OK static CPU manager in effect')
PY
)
  status=$?
  echo "$node (bench-cell=$cell)"
  echo "$verdict"
  [ $status -eq 0 ] || rc=1
done <<< "$nodes"

if [ $rc -ne 0 ]; then
  echo "REFUSING: at least one app node is not configured for exclusive CPUs"
fi
exit $rc
