#!/usr/bin/env bash
# Delete the benchmark cluster and nothing else. Refuses unless the cluster
# carries the tags this kit created it with, so it cannot be pointed at a
# cluster that merely shares the name.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$HERE/cluster.yaml"
CLUSTER=$(awk '/^metadata:/{m=1} m && /^  name:/{print $2; exit}' "$CONFIG")
REGION=$(awk '/^metadata:/{m=1} m && /^  region:/{print $2; exit}' "$CONFIG")

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

log "verifying $CLUSTER in $REGION before deleting anything"
# Distinguish "no such cluster" from "call failed": swallowing an auth error
# here reports a successful teardown while the cluster keeps billing.
err=$(mktemp)
tags=$(aws eks describe-cluster --region "$REGION" --name "$CLUSTER" \
       --query 'cluster.tags' --output json 2>"$err")
rc=$?
if [ $rc -ne 0 ]; then
  if grep -q 'ResourceNotFoundException' "$err"; then
    log "cluster $CLUSTER not found - nothing to do"
    rm -f "$err"
    exit 0
  fi
  log "REFUSING: could not describe $CLUSTER, so it may still be running:"
  sed 's/^/    /' "$err" >&2
  rm -f "$err"
  exit 1
fi
rm -f "$err"
if [ -z "$tags" ] || [ "$tags" = "null" ]; then
  log "REFUSING: $CLUSTER exists but carries no tags; not deleting blind"
  exit 1
fi
purpose=$(echo "$tags" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("purpose",""))')
if [ "$purpose" != "benchmark-app" ]; then
  log "REFUSING: $CLUSTER has purpose='$purpose', not created by this kit"
  exit 1
fi

log "deleting $CLUSTER (nodegroups first, then control plane and VPC)"
eksctl delete cluster -f "$CONFIG" --disable-nodegroup-eviction --wait

log "remaining clusters in $REGION (should not include $CLUSTER):"
aws eks list-clusters --region "$REGION" --query 'clusters' --output text
kubectl config delete-context "${KUBE_CONTEXT:-benchmark-app}" >/dev/null 2>&1 || true
log "teardown complete"
