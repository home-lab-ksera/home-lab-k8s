#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOMATION_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 共通ログ出力
# shellcheck source=automation/scripts/common-logging.sh
source "$AUTOMATION_DIR/scripts/common-logging.sh"

usage() {
  cat <<'USAGE'
Usage: upgrade-gate-check.sh --phase <pre|post> [--verbose]

Options:
  --phase    実行フェーズ (pre / post)
  --verbose  詳細ログを表示
USAGE
}

phase=""
verbose="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase)
      phase="${2:-}"
      shift 2
      ;;
    --verbose)
      verbose="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      log_error "不明な引数: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ "$phase" != "pre" && "$phase" != "post" ]]; then
  log_error "--phase は pre または post を指定してください"
  usage
  exit 1
fi

# 設定読み込み
if [ -f "$AUTOMATION_DIR/scripts/settings-loader.sh" ]; then
  # shellcheck source=/dev/null
  source "$AUTOMATION_DIR/scripts/settings-loader.sh" load 2>/dev/null || true
fi

k8s_user="$(get_config kubernetes user "k8suser")"
control_plane_ip="$(get_config network control_plane_ip "192.168.122.10")"
ssh_key_path="$(get_config kubernetes ssh_key_path "")"

# shellcheck source=automation/scripts/common-ssh.sh
source "$AUTOMATION_DIR/scripts/common-ssh.sh"
ssh_opts=()
k8s_ssh_opts ssh_opts "$ssh_key_path"

ssh_cmd=(ssh "${ssh_opts[@]}" "${k8s_user}@${control_plane_ip}")

run_remote() {
  local command="$1"
  "${ssh_cmd[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf bash -lc '$command'"
}

has_error="false"

log_status "=== Upgrade Gate Check (${phase}) ==="

log_status "[1/3] Node Ready 状態を確認中..."
not_ready_nodes="$(run_remote "kubectl get nodes --no-headers | awk '\''\$2 != \"Ready\" {print \$1\" \"\$2}'\''")"
if [[ -n "$not_ready_nodes" ]]; then
  log_error "Readyでないノードがあります:"
  printf '%s\n' "$not_ready_nodes" >&2
  has_error="true"
fi

log_status "[2/3] 異常Pod状態を確認中..."
bad_pods="$(run_remote "kubectl get pods -A --no-headers | awk '\''\$4 ~ /(CrashLoopBackOff|ImagePullBackOff|Error|CreateContainerConfigError|RunContainerError)/ {print \$1\"/\"\$2\" status=\"\$4}'\''")"
if [[ -n "$bad_pods" ]]; then
  log_error "異常状態のPodがあります:"
  printf '%s\n' "$bad_pods" >&2
  has_error="true"
fi

log_status "[3/3] ArgoCD Application 同期状態を確認中..."
argocd_apps_count="$(run_remote "kubectl get applications -n argocd --no-headers 2>/dev/null | wc -l")"
if [[ "${argocd_apps_count:-0}" -eq 0 ]]; then
  log_error "argocd namespace に Application が見つかりません"
  has_error="true"
else
  unsynced_apps="$(run_remote "kubectl get applications -n argocd -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status --no-headers | awk '\''\$2 != \"Synced\" || \$3 != \"Healthy\" {print \$1\" sync=\"\$2\" health=\"\$3}'\''")"
  if [[ -n "$unsynced_apps" ]]; then
    log_error "Synced/Healthy ではない Application があります:"
    printf '%s\n' "$unsynced_apps" >&2
    has_error="true"
  fi
fi

if [[ "$verbose" == "true" ]]; then
  log_status "--- nodes ---"
  run_remote "kubectl get nodes -o wide"
  log_status "--- pods ---"
  run_remote "kubectl get pods -A"
  log_status "--- applications ---"
  run_remote "kubectl get applications -n argocd -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status"
fi

if [[ "$has_error" == "true" ]]; then
  log_error "Upgrade Gate Check (${phase}) 失敗"
  exit 1
fi

log_status "Upgrade Gate Check (${phase}) 成功"
