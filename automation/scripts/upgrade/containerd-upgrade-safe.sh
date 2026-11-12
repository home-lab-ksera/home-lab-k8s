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

current_version="$(get_config upgrade containerd_current_version "1.7.28-0ubuntu1~24.04.2")"
target_version="$(get_config upgrade containerd_target_version "2.2.3-1~ubuntu.24.04~noble")"
canary_node="$(get_config upgrade containerd_canary_node "k8s-worker2")"
package_name="$(get_config upgrade containerd_package "containerd.io")"
source_channel="$(get_config upgrade containerd_source_channel "docker")"

usage() {
  cat <<'USAGE'
Usage: containerd-upgrade-safe.sh [--rollback] [--node <node-name>]

Options:
  --rollback         containerd を現行バージョンへロールバック
  --node <name>      対象ノード名（既定: settings upgrade.containerd_canary_node）
USAGE
}

do_rollback="false"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rollback)
      do_rollback="true"
      shift
      ;;
    --node)
      canary_node="${2:-}"
      if [[ -z "$canary_node" ]]; then
        log_error "--node の値が未指定です"
        exit 1
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      log_error "不明なオプション: $1"
      usage
      exit 1
      ;;
  esac
done

ssh_opts=(
  -o StrictHostKeyChecking=no
  -o BatchMode=yes
  -o LogLevel=ERROR
)

if [[ -n "$ssh_key_path" ]]; then
  ssh_opts+=("-i" "$ssh_key_path")
fi

control_ssh=(ssh "${ssh_opts[@]}" "${k8s_user}@${control_plane_ip}")

resolve_node_ip() {
  local node_name="$1"
  case "$node_name" in
    k8s-worker2) echo "$worker_2_ip" ;;
    k8s-control-plane) echo "$control_plane_ip" ;;
    *)
      log_error "未対応ノード名です: ${node_name}"
      exit 1
      ;;
  esac
}

check_node_ready() {
  local node_name="$1"
  "${control_ssh[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get node ${node_name} -o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}'" | grep -q True
}

run_post_gate() {
  "$AUTOMATION_DIR/scripts/upgrade/upgrade-gate-check.sh" --phase post
}

ensure_docker_repo_if_needed() {
  local ssh_cmd=("$@")
  if [[ "$source_channel" != "docker" ]]; then
    return 0
  fi

  "${ssh_cmd[@]}" "sudo install -m 0755 -d /etc/apt/keyrings"
  "${ssh_cmd[@]}" "if [ ! -f /etc/apt/keyrings/docker.gpg ]; then curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg; fi"
  "${ssh_cmd[@]}" "sudo chmod a+r /etc/apt/keyrings/docker.gpg"
  "${ssh_cmd[@]}" "echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu noble stable\" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null"
}

target_ip="$(resolve_node_ip "$canary_node")"
target_ssh=(ssh "${ssh_opts[@]}" "${k8s_user}@${target_ip}")

log_status "=== Containerd Upgrade Safe ==="
log_status "対象ノード: ${canary_node} (${target_ip})"

if [[ "$do_rollback" == "true" ]]; then
  log_warning "ロールバックモードで実行します: ${current_version}"
else
  log_status "更新モードで実行します: ${target_version}"
fi

log_status "対象パッケージ: ${package_name} (channel=${source_channel})"

"${control_ssh[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl cordon ${canary_node}"
"${control_ssh[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl drain ${canary_node} --ignore-daemonsets --delete-emptydir-data"

if [[ "$do_rollback" == "true" ]]; then
  ensure_docker_repo_if_needed "${target_ssh[@]}"
  "${target_ssh[@]}" "sudo apt-get update -y"
  "${target_ssh[@]}" "sudo apt-mark unhold containerd containerd.io || true"
  "${target_ssh[@]}" "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --allow-downgrades -o Dpkg::Options::=\"--force-confold\" ${package_name}=${current_version}"
else
  ensure_docker_repo_if_needed "${target_ssh[@]}"
  "${target_ssh[@]}" "sudo apt-get update -y"
  "${target_ssh[@]}" "sudo apt-mark unhold containerd containerd.io || true"
  "${target_ssh[@]}" "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -o Dpkg::Options::=\"--force-confold\" ${package_name}=${target_version}"
fi

"${target_ssh[@]}" "sudo systemctl restart containerd kubelet"

log_status "CRI疎通確認中..."
"${target_ssh[@]}" "sudo crictl info >/dev/null"

log_status "kubelet エラーログ確認中..."
if "${target_ssh[@]}" "sudo journalctl -u kubelet --since '3 minutes ago' --no-pager | grep -q 'runtime.v1.RuntimeService'"; then
  log_error "kubelet に runtime.v1.RuntimeService エラーを検出しました"
  exit 1
fi

log_status "ノードReady復帰を確認中..."
for _ in {1..24}; do
  if check_node_ready "$canary_node"; then
    break
  fi
  sleep 5
done

if ! check_node_ready "$canary_node"; then
  log_error "${canary_node} が Ready に復帰しません"
  exit 1
fi

"${control_ssh[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl uncordon ${canary_node}"
"${control_ssh[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes -o wide"

log_status "Post gate 実行中..."
run_post_gate

log_status "Containerd Upgrade Safe 完了"
