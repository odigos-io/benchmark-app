#!/usr/bin/env bash
# Installs Odigos with the Helm chart. Uses your current kubectl context unless
# KUBE_CONTEXT is set, and the latest chart unless CHART_VERSION is set.
#
#   ODIGOS_TOKEN=<your Odigos enterprise token> infra/install-odigos.sh
#
# The published results were produced with Odigos 1.36. Record the version you
# install alongside your results: a different version measures a different agent.
set -euo pipefail
: "${ODIGOS_TOKEN:?ODIGOS_TOKEN must be set (your Odigos enterprise token)}"

HELM=(helm); KUBECTL=(kubectl)
if [ -n "${KUBE_CONTEXT:-}" ]; then
  HELM+=(--kube-context "$KUBE_CONTEXT"); KUBECTL+=(--context "$KUBE_CONTEXT")
fi

args=(--set onPremToken="$ODIGOS_TOKEN")
[ -n "${CHART_VERSION:-}" ] && args+=(--version "$CHART_VERSION")
[ -n "${ODIGLET_IMAGE:-}" ] && args+=(--set images.enterprise-odiglet="$ODIGLET_IMAGE")
[ -n "${AGENTS_IMAGE:-}" ] && args+=(--set images.enterprise-agents="$AGENTS_IMAGE")

helm repo add odigos https://odigos-io.github.io/odigos/ >/dev/null 2>&1 || true
helm repo update odigos >/dev/null

"${HELM[@]}" upgrade --install odigos odigos/odigos \
  -n odigos-system --create-namespace "${args[@]}" --timeout 15m

"${KUBECTL[@]}" -n odigos-system rollout status ds/odiglet --timeout=10m
"${KUBECTL[@]}" -n odigos-system get pods -o wide
"${HELM[@]}" -n odigos-system list -f '^odigos$'
