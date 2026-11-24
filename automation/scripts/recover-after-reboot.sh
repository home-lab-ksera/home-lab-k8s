#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=automation/scripts/common-logging.sh
source "${SCRIPT_DIR}/common-logging.sh"

if [[ -f "${SCRIPT_DIR}/settings-loader.sh" ]]; then
  # shellcheck source=/dev/null
  source "${SCRIPT_DIR}/settings-loader.sh" load 2>/dev/null || true
fi

CONTROL_PLANE_IP="${K8S_CONTROL_PLANE_IP:-192.168.122.10}"
K8S_USER="${K8S_USER:-k8suser}"
VM_PREFIX="${K8S_VM_PREFIX:-k8s}"
MAX_WAIT_SECONDS="${RECOVER_MAX_WAIT_SECONDS:-300}"
CHECK_INTERVAL_SECONDS="${RECOVER_CHECK_INTERVAL_SECONDS:-10}"

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    log_error "必要なコマンドが見つかりません: ${cmd}"
    exit 1
  fi
}

start_k8s_vms() {
  local vm_names
  vm_names="$(virsh list --all --name | awk -v prefix="${VM_PREFIX}-" 'index($0, prefix) == 1 { print }')"

  if [[ -z "$vm_names" ]]; then
    log_error "${VM_PREFIX}- で始まる VM が見つかりません"
    exit 1
  fi

  log_status "k8s VM を起動します"
  while IFS= read -r vm_name; do
    [[ -z "$vm_name" ]] && continue
    local state
    state="$(virsh domstate "$vm_name" | tr -d '[:space:]')"
    if [[ "$state" == "running" ]]; then
      log_status "すでに起動済み: ${vm_name}"
      continue
    fi
    virsh start "$vm_name" >/dev/null
    log_status "起動: ${vm_name}"
  done <<< "$vm_names"
}

wait_for_api_ready() {
  local deadline
  deadline=$((SECONDS + MAX_WAIT_SECONDS))

  log_status "Kubernetes API 応答待ち (${MAX_WAIT_SECONDS}秒まで)"
  until kubectl get nodes >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      log_error "タイムアウト: Kubernetes API に接続できません"
      return 1
    fi
    sleep "$CHECK_INTERVAL_SECONDS"
  done
  log_success "Kubernetes API への接続を確認しました"
}

wait_for_nodes_ready() {
  local deadline
  deadline=$((SECONDS + MAX_WAIT_SECONDS))

  log_status "全ノード Ready 待ち (${MAX_WAIT_SECONDS}秒まで)"
  while true; do
    local not_ready_count
    not_ready_count="$(kubectl get nodes --no-headers 2>/dev/null | awk '$2 !~ /^Ready/ { count++ } END { print count + 0 }')"
    local total_count
    total_count="$(kubectl get nodes --no-headers 2>/dev/null | awk 'NF { count++ } END { print count + 0 }')"

    if [[ "$total_count" -gt 0 && "$not_ready_count" -eq 0 ]]; then
      log_success "全ノード Ready: ${total_count}"
      return 0
    fi

    if (( SECONDS >= deadline )); then
      log_warning "ノードが Ready になりきっていません（未Ready: ${not_ready_count}/${total_count}）"
      kubectl get nodes -o wide || true
      return 1
    fi
    sleep "$CHECK_INTERVAL_SECONDS"
  done
}

print_post_status() {
  log_status "VM 状態"
  virsh list --all

  log_status "ノード状態"
  kubectl get nodes -o wide

  log_status "主要 Pod 状態（Running/Completed 以外）"
  ssh -o StrictHostKeyChecking=no "${K8S_USER}@${CONTROL_PLANE_IP}" \
    "kubectl get pods -A --no-headers | awk '\$4 != \"Running\" && \$4 != \"Completed\" { print \$1, \$2, \$4 }'" || true
}

main() {
  require_command virsh
  require_command kubectl
  require_command ssh

  log_status "=== Ubuntu再起動後の k8s 復旧開始 ==="
  start_k8s_vms

  if ! wait_for_api_ready; then
    print_post_status
    exit 1
  fi

  wait_for_nodes_ready || true
  print_post_status
  log_success "=== 復旧処理を完了しました ==="
}

main "$@"
