#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOMATION_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$AUTOMATION_DIR/scripts/common-logging.sh" ]; then
  # shellcheck source=/dev/null
  source "$AUTOMATION_DIR/scripts/common-logging.sh"
else
  log_status() { echo "$@"; }
  log_error() { echo "$@"; }
  log_warning() { echo "$@"; }
fi

if [ -f "$AUTOMATION_DIR/scripts/settings-loader.sh" ]; then
  # shellcheck source=/dev/null
  source "$AUTOMATION_DIR/scripts/settings-loader.sh" load 2>/dev/null || true
fi

k8s_user="$(get_config kubernetes user "k8suser")"
control_plane_ip="$(get_config network control_plane_ip "192.168.122.10")"
worker_2_ip="$(get_config network worker_2_ip "192.168.122.12")"
ssh_key_path="$(get_config kubernetes ssh_key_path "")"
target_version="$(get_config upgrade containerd_target_version "2.2.3-1~ubuntu.24.04~noble")"
current_version="$(get_config upgrade containerd_current_version "1.7.28-0ubuntu1~24.04.2")"
package_name="$(get_config upgrade containerd_package "containerd.io")"

ssh_opts=(
  -o StrictHostKeyChecking=no
  -o BatchMode=yes
  -o LogLevel=ERROR
)

if [[ -n "$ssh_key_path" ]]; then
  ssh_opts+=("-i" "$ssh_key_path")
fi

control_ssh=(ssh "${ssh_opts[@]}" "${k8s_user}@${control_plane_ip}")

nodes=(
  "$control_plane_ip"
  "$worker_2_ip"
)

log_status "=== Containerd Precheck ==="
log_status "現行想定バージョン: ${current_version}"
log_status "更新候補バージョン: ${target_version}"
log_status "対象パッケージ: ${package_name}"

"${control_ssh[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes -o wide"

for node_ip in "${nodes[@]}"; do
  node_ssh=(ssh "${ssh_opts[@]}" "${k8s_user}@${node_ip}")
  node_name="$("${node_ssh[@]}" hostname)"

  log_status "ノード診断: ${node_name} (${node_ip})"
  "${node_ssh[@]}" "sudo containerd --version"
  "${node_ssh[@]}" "sudo apt-cache policy containerd | sed -n '1,10p'"
  "${node_ssh[@]}" "sudo apt-cache policy containerd.io | sed -n '1,10p' || true"
  "${node_ssh[@]}" "sudo crictl info >/dev/null && echo 'crictl: OK'"
  "${node_ssh[@]}" "sudo systemctl is-active containerd kubelet"
  "${node_ssh[@]}" "sudo journalctl -u kubelet -n 80 --no-pager | egrep -i 'runtime.v1.RuntimeService|failed to run Kubelet|containerd' || true"
  "${node_ssh[@]}" "sudo grep -E 'sandbox_image|SystemdCgroup' /etc/containerd/config.toml || true"
done

log_status "ArgoCD全Application状態を確認中..."
"${control_ssh[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get applications -n argocd -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status"

log_status "Containerd Precheck 完了"
