#!/usr/bin/env bash
# Create the reference benchmark cluster on EKS from infra/aws/cluster.yaml and
# register a kubeconfig context for it (default name: benchmark-app).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$HERE/cluster.yaml"
CLUSTER=$(awk '/^metadata:/{m=1} m && /^  name:/{print $2; exit}' "$CONFIG")
REGION=$(awk '/^metadata:/{m=1} m && /^  region:/{print $2; exit}' "$CONFIG")
CONTEXT="${KUBE_CONTEXT:-benchmark-app}"

aws sts get-caller-identity --query Arn --output text

eksctl create cluster -f "$CONFIG"

aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION" --alias "$CONTEXT"
kubectl --context "$CONTEXT" get nodes -L bench-cell,bench-deps,bench-lane -L topology.kubernetes.io/zone

echo
echo "next: kubectl config use-context $CONTEXT, then infra/verify-nodes.sh and infra/install-odigos.sh"
