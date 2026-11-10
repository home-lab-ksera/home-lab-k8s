#!/usr/bin/env bash

# GitOps bootstrap entrypoint.
# kubelet serving 証明書の準備後、ArgoCD、pre-ESO 最小 Secret、root Application を初期導入する。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOMATION_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$AUTOMATION_DIR/.." && pwd)"

# shellcheck source=../scripts/common-logging.sh
source "$AUTOMATION_DIR/scripts/common-logging.sh"

if [[ -f "$AUTOMATION_DIR/scripts/settings-loader.sh" ]]; then
  # shellcheck source=../scripts/settings-loader.sh
  source "$AUTOMATION_DIR/scripts/settings-loader.sh" load 2>/dev/null || true
fi

CONTROL_PLANE_IP="${K8S_CONTROL_PLANE_IP:-192.168.122.10}"
K8S_USER="${K8S_USER:-k8suser}"
REMOTE_TMP="/tmp/k8s-myhome-bootstrap"
APP_OF_APPS_SRC="$ROOT_DIR/manifests/bootstrap/app-of-apps.yaml"
ARGOCD_UPSTREAM_KUSTOMIZATION="$ROOT_DIR/manifests/platform/argocd-config/upstream/kustomization.yaml"
# 初回導入も GitOps と同じ固定版を使い、再 bootstrap での巻き戻しを防ぐ。
ARGOCD_BOOTSTRAP_VERSION="$(awk -F/ '/https:\/\/raw.githubusercontent.com\/argoproj\/argo-cd\// { print $(NF-2) }' "$ARGOCD_UPSTREAM_KUSTOMIZATION")"
if [[ ! "$ARGOCD_BOOTSTRAP_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  log_error "ArgoCD の固定バージョンを取得できません: $ARGOCD_UPSTREAM_KUSTOMIZATION"
  exit 1
fi

ssh_cmd=(ssh -T -o StrictHostKeyChecking=no -o BatchMode=yes -o LogLevel=ERROR "${K8S_USER}@${CONTROL_PLANE_IP}")
scp_cmd=(scp -o StrictHostKeyChecking=no -o LogLevel=ERROR)

require_file() {
  local file_path="$1"
  if [[ ! -f "$file_path" ]]; then
    log_error "必要なファイルが見つかりません: $file_path"
    exit 1
  fi
}

copy_manifest() {
  local src="$1"
  local dst_name="$2"
  "${scp_cmd[@]}" "$src" "${K8S_USER}@${CONTROL_PLANE_IP}:${REMOTE_TMP}/${dst_name}"
}

log_status "=== GitOps bootstrap 開始 ==="
log_status "対象 control-plane: ${K8S_USER}@${CONTROL_PLANE_IP}"
log_status "ArgoCD bootstrap version: ${ARGOCD_BOOTSTRAP_VERSION}"

require_file "$APP_OF_APPS_SRC"

if ! "${ssh_cmd[@]}" 'kubectl get nodes' >/dev/null 2>&1; then
  log_error "k8sクラスタに接続できません。make phase2 を先に完了してください。"
  exit 1
fi

if [[ -z "${PULUMI_ACCESS_TOKEN:-}" && -n "${PULUMI_PULUMI_ACCESS_TOKEN:-}" ]]; then
  PULUMI_ACCESS_TOKEN="$PULUMI_PULUMI_ACCESS_TOKEN"
  export PULUMI_ACCESS_TOKEN
fi

if [[ -z "${PULUMI_ACCESS_TOKEN:-}" ]]; then
  log_error "PULUMI_ACCESS_TOKEN が未設定です。automation/settings.toml または環境変数に設定してください。"
  exit 1
fi

log_status "kubelet serving 証明書を準備・検証中..."
bash "$AUTOMATION_DIR/scripts/kubelet-serving-certificates.sh" --prepare

"${ssh_cmd[@]}" "mkdir -p ${REMOTE_TMP}"
copy_manifest "$APP_OF_APPS_SRC" "app-of-apps.yaml"

log_status "ArgoCD を初期導入中..."
"${ssh_cmd[@]}" "ARGOCD_BOOTSTRAP_VERSION=${ARGOCD_BOOTSTRAP_VERSION} bash -s" <<'REMOTE'
set -euo pipefail

kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side --force-conflicts -n argocd -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_BOOTSTRAP_VERSION}/manifests/install.yaml"
kubectl apply --server-side -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_BOOTSTRAP_VERSION}/manifests/crds/applicationset-crd.yaml"
kubectl get crd applicationsets.argoproj.io >/dev/null
kubectl rollout status deployment/argocd-server -n argocd --timeout=300s
kubectl patch configmap argocd-cmd-params-cm -n argocd --type merge -p '{"data":{"server.insecure":"true"}}'
REMOTE

log_status "pre-ESO Secret を作成中..."
pulumi_token_escaped="$(printf '%q' "$PULUMI_ACCESS_TOKEN")"
"${ssh_cmd[@]}" "PULUMI_ACCESS_TOKEN=${pulumi_token_escaped} bash -s" <<'REMOTE'
set -euo pipefail

kubectl create namespace external-secrets-system --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic pulumi-esc-token \
  --namespace external-secrets-system \
  --from-literal=accessToken="${PULUMI_ACCESS_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f - <<'RBAC'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: external-secrets-kubernetes-provider
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["serviceaccounts"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["serviceaccounts/token"]
    verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: external-secrets-kubernetes-provider
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: external-secrets-kubernetes-provider
subjects:
  - kind: ServiceAccount
    name: external-secrets-operator
    namespace: external-secrets-system
RBAC
REMOTE

log_status "root Application を適用中..."
"${ssh_cmd[@]}" <<'REMOTE'
set -euo pipefail

kubectl apply -f /tmp/k8s-myhome-bootstrap/app-of-apps.yaml
kubectl get application bootstrap-root -n argocd >/dev/null
kubectl get applications -n argocd --no-headers 2>/dev/null | awk '{print "  - " $1 " (" $2 "/" $3 ")"}' || true
REMOTE

log_status "✓ GitOps bootstrap 完了"
log_status "以降の core / infrastructure / platform / access / apps 収束は ArgoCD child Application が管理します。"
