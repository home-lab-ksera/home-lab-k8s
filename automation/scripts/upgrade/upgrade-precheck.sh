#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOMATION_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 共通ログ出力
if [ -f "$AUTOMATION_DIR/scripts/common-logging.sh" ]; then
  # shellcheck source=/dev/null
  source "$AUTOMATION_DIR/scripts/common-logging.sh"
else
  log_status() { echo "$@"; }
  log_error() { echo "$@"; }
  log_warning() { echo "$@"; }
fi

# 設定読み込み
if [ -f "$AUTOMATION_DIR/scripts/settings-loader.sh" ]; then
  # shellcheck source=/dev/null
  source "$AUTOMATION_DIR/scripts/settings-loader.sh" load 2>/dev/null || true
fi

# shellcheck source=automation/scripts/upgrade/kubernetes-version.sh
source "$AUTOMATION_DIR/scripts/upgrade/kubernetes-version.sh"
target_version="$(resolve_target_version)"
apt_channel="$(get_config upgrade apt_channel "")"
k8s_user="$(get_config kubernetes user "k8suser")"
control_plane_ip="$(get_config network control_plane_ip "192.168.122.10")"
ssh_key_path="$(get_config kubernetes ssh_key_path "")"

target_minor="${target_version#v}"
target_minor="${target_minor%.*}"
if [[ -z "$apt_channel" ]]; then
  apt_channel="v${target_minor}"
fi

ssh_opts=(
  -o StrictHostKeyChecking=no
  -o BatchMode=yes
  -o LogLevel=ERROR
)

if [[ -n "$ssh_key_path" ]]; then
  ssh_opts+=("-i" "$ssh_key_path")
fi

ssh_cmd=(ssh "${ssh_opts[@]}" "${k8s_user}@${control_plane_ip}")

log_status "=== Upgrade Precheck ==="
log_status "対象バージョン: ${target_version}"
log_status "APTチャネル: ${apt_channel}"

log_status "Control Plane 接続確認中..."
"${ssh_cmd[@]}" "true"

log_status "クラスタ健全性確認中..."
"${ssh_cmd[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes -o wide"
"${ssh_cmd[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get pods -A"

log_status "etcd スナップショット取得中..."
"${ssh_cmd[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf bash -c 'set -euo pipefail; pod=\"\$(kubectl -n kube-system get pods -l component=etcd -o jsonpath={.items[0].metadata.name})\"; ts=\"\$(date +%Y%m%d-%H%M%S)\"; kubectl -n kube-system exec \"\$pod\" -- etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key snapshot save /var/lib/etcd/etcd-snapshot-\$ts.db'"

log_status "kubeadm upgrade plan 実行中..."
"${ssh_cmd[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubeadm upgrade plan"

log_status "ArgoCD 同期状態確認中..."
"${ssh_cmd[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get applications -n argocd"

log_status "Upgrade Precheck 完了"
