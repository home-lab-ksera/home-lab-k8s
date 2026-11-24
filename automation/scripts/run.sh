#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOMATION_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="$AUTOMATION_DIR/run.log"

# 共通ログ出力
# shellcheck source=automation/scripts/common-logging.sh
source "$SCRIPT_DIR/common-logging.sh"

run_step() {
  local name="$1"
  shift
  log_status "=== ${name} ==="
  {
    "$@"
  } 2>&1 | tee -a "$LOG_FILE"
  return ${PIPESTATUS[0]}
}

ensure_sudo() {
  if ! sudo -v; then
    log_error "sudo権限の取得に失敗しました"
    exit 1
  fi
}

with_settings() {
  if [ -f "$SCRIPT_DIR/settings-loader.sh" ] && [ -f "$AUTOMATION_DIR/settings.toml" ]; then
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/settings-loader.sh" load 2>/dev/null || true
  fi
  "$@"
}

run_gate_with_retry() {
  local phase="$1"
  local label="$2"
  local retries="3"
  local wait_seconds="30"

  if declare -f get_config >/dev/null 2>&1; then
    retries="$(get_config upgrade gate_retries "3")"
    wait_seconds="$(get_config upgrade gate_retry_wait_seconds "30")"
  fi

  if [[ ! "$retries" =~ ^[0-9]+$ ]] || [[ "$retries" -lt 1 ]]; then
    retries="3"
  fi
  if [[ ! "$wait_seconds" =~ ^[0-9]+$ ]] || [[ "$wait_seconds" -lt 1 ]]; then
    wait_seconds="30"
  fi

  local attempt=1
  while [[ "$attempt" -le "$retries" ]]; do
    if run_step "$label (attempt ${attempt}/${retries})" with_settings "$SCRIPT_DIR/upgrade/upgrade-gate-check.sh" --phase "$phase"; then
      return 0
    fi

    if [[ "$attempt" -lt "$retries" ]]; then
      log_warning "$label 失敗。${wait_seconds}秒後に再試行します"
      sleep "$wait_seconds"
    fi
    attempt=$((attempt + 1))
  done

  return 1
}

usage() {
  cat << 'USAGE'
Usage: ./scripts/run.sh <phase>

Phases:
  all                 - phase1 -> phase2 -> bootstrap -> phase5を順番に実行
  phase1|vm           - VMの構成（host-setup）
  phase2|k8s          - k8sの構成（infrastructure）
  bootstrap           - GitOps bootstrap（ArgoCD + root Application）
  phase5|verify       - 確認（verify）
  upgrade             - k8sアップグレード（完全自動）
  upgrade-safe        - ゲートチェック付きアップグレード
  upgrade-precheck    - アップグレード事前チェック
  upgrade-control-plane - コントロールプレーン更新
  upgrade-workers     - ワーカーノード更新
  upgrade-postcheck   - アップグレード後チェック
  containerd-precheck - containerd更新 事前診断
  containerd-safe     - containerd更新（カナリア/ロールバック対応）

Log:
  automation/run.log
USAGE
}

main() {
  local phase="${1:-all}"

  log_status "ログ: $LOG_FILE"
  log_status "開始: $(date '+%Y-%m-%d %H:%M:%S')"

  case "$phase" in
    all)
      ensure_sudo
      run_step "Phase 1: VM" with_settings bash -c "cd \"$AUTOMATION_DIR/host-setup\" && ./setup-host.sh"
      run_step "Phase 2: k8s" with_settings bash -c "cd \"$AUTOMATION_DIR/infrastructure\" && ./clean-and-deploy.sh"
      run_step "Bootstrap: GitOps" with_settings bash -c "cd \"$AUTOMATION_DIR/platform\" && ./platform-deploy.sh"
      run_step "Phase 5: Verify" with_settings "$SCRIPT_DIR/verify.sh"
      ;;
    phase1|vm)
      ensure_sudo
      run_step "Phase 1: VM" with_settings bash -c "cd \"$AUTOMATION_DIR/host-setup\" && ./setup-host.sh"
      ;;
    phase2|k8s)
      run_step "Phase 2: k8s" with_settings bash -c "cd \"$AUTOMATION_DIR/infrastructure\" && ./clean-and-deploy.sh"
      ;;
    bootstrap)
      run_step "Bootstrap: GitOps" with_settings bash -c "cd \"$AUTOMATION_DIR/platform\" && ./platform-deploy.sh"
      ;;
    phase5|verify)
      run_step "Phase 5: Verify" with_settings "$SCRIPT_DIR/verify.sh"
      ;;
    upgrade)
      run_step "Upgrade: Precheck" with_settings "$SCRIPT_DIR/upgrade/upgrade-precheck.sh"
      run_step "Upgrade: Control Plane" with_settings "$SCRIPT_DIR/upgrade/upgrade-control-plane.sh"
      run_step "Upgrade: Workers" with_settings "$SCRIPT_DIR/upgrade/upgrade-workers.sh"
      run_step "Upgrade: Postcheck" with_settings "$SCRIPT_DIR/upgrade/upgrade-postcheck.sh"
      ;;
    upgrade-safe)
      run_gate_with_retry pre "Upgrade Gate: Pre"
      run_step "Upgrade: Precheck" with_settings "$SCRIPT_DIR/upgrade/upgrade-precheck.sh"
      run_step "Upgrade: Control Plane" with_settings "$SCRIPT_DIR/upgrade/upgrade-control-plane.sh"
      run_step "Upgrade: Workers" with_settings "$SCRIPT_DIR/upgrade/upgrade-workers.sh"
      run_step "Upgrade: Postcheck" with_settings "$SCRIPT_DIR/upgrade/upgrade-postcheck.sh"
      run_gate_with_retry post "Upgrade Gate: Post"
      ;;
    upgrade-precheck)
      run_step "Upgrade: Precheck" with_settings "$SCRIPT_DIR/upgrade/upgrade-precheck.sh"
      ;;
    upgrade-control-plane)
      run_step "Upgrade: Control Plane" with_settings "$SCRIPT_DIR/upgrade/upgrade-control-plane.sh"
      ;;
    upgrade-workers)
      run_step "Upgrade: Workers" with_settings "$SCRIPT_DIR/upgrade/upgrade-workers.sh"
      ;;
    upgrade-postcheck)
      run_step "Upgrade: Postcheck" with_settings "$SCRIPT_DIR/upgrade/upgrade-postcheck.sh"
      ;;
    containerd-precheck)
      run_step "Containerd: Precheck" with_settings "$SCRIPT_DIR/upgrade/containerd-precheck.sh"
      ;;
    containerd-safe)
      run_step "Containerd: Safe Upgrade" with_settings "$SCRIPT_DIR/upgrade/containerd-upgrade-safe.sh"
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      log_error "不明なフェーズ: $phase"
      usage
      exit 1
      ;;
  esac
}

main "$@"
