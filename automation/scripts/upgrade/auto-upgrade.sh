#!/usr/bin/env bash
# kubernetes-version.txt の版にクラスタを揃える。ホストの k8s-auto-upgrade.timer から 1 時間ごとに実行する。
# PR のマージが承認の代わりで、ここでは安全に進められる場合だけ upgrade-safe を実行する。
#
# - 版が揃っている            : 何もしない
# - パッケージが未公開        : 何もしない（次回に再判定）
# - 事前ゲートが通らない      : 版ごとに 1 回だけ通知して、次回に再判定
# - 版の後退 / minor の飛ばし / ノード間の版の混在 / 更新の失敗:
#   通知して停止フラグを立てる。人が確認して --clear-failure するまで何もしない
set -euo pipefail

UPGRADE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPGRADE_AUTOMATION_DIR="$(cd "$UPGRADE_DIR/../.." && pwd)"

# shellcheck source=automation/scripts/common-logging.sh
source "$UPGRADE_AUTOMATION_DIR/scripts/common-logging.sh"
if [[ -f "$UPGRADE_AUTOMATION_DIR/settings.toml" ]]; then
  # shellcheck source=automation/scripts/settings-loader.sh
  QUIET_LOGS=true source "$UPGRADE_AUTOMATION_DIR/scripts/settings-loader.sh" load 2>/dev/null || true
fi
if ! declare -f get_config >/dev/null 2>&1; then
  get_config() { printf '%s\n' "${3:-}"; }
fi
# shellcheck source=automation/scripts/upgrade/kubernetes-version.sh
source "$UPGRADE_DIR/kubernetes-version.sh"

usage() {
  cat <<'USAGE'
Usage: auto-upgrade.sh [--dry-run | --clear-failure]

  (なし)           判定して、必要なら upgrade-safe を実行する
  --dry-run        判定だけ表示する（更新も通知もしない）
  --clear-failure  停止フラグを消す（原因を確かめてから使う）
USAGE
}

mode="run"
case "${1:-}" in
  "") ;;
  --dry-run) mode="dry-run" ;;
  --clear-failure) mode="clear" ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

state_dir="${K8S_AUTO_UPGRADE_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/k8s-auto-upgrade}"
failure_file="$state_dir/failed"
gate_notice_file="$state_dir/gate-blocked"
mkdir -p "$state_dir"

if [[ "$mode" == "clear" ]]; then
  rm -f "$failure_file" "$gate_notice_file"
  log_status "停止フラグを消しました。次回の timer から判定を再開します"
  exit 0
fi

# timer と手動実行が重ならないようにする
exec 9>"$state_dir/lock"
if ! flock -n 9; then
  log_warning "別の auto-upgrade が実行中です"
  exit 0
fi

webhook_url="$(get_config auto_upgrade slack_webhook_url "")"

json_escape() {
  local text="$1"
  text="${text//\\/\\\\}"
  text="${text//\"/\\\"}"
  text="${text//$'\n'/\\n}"
  printf '%s' "$text"
}

notify() {
  local message="[k8s-auto-upgrade] $1"
  log_status "通知: $1"
  [[ "$mode" == "run" && -n "$webhook_url" ]] || return 0
  curl -fsS -m 20 -X POST -H 'Content-Type: application/json' \
    --data "{\"text\":\"$(json_escape "$message")\"}" "$webhook_url" >/dev/null \
    || log_warning "Slack への通知に失敗しました"
}

# 人の確認が要る状態。以後の timer 実行は止まる。
stop_with_failure() {
  local reason="$1"
  if [[ "$mode" == "run" ]]; then
    printf '%s %s\n' "$(date -Is)" "$reason" >"$failure_file"
  fi
  notify "停止しました: ${reason}
確認後、ホストで \`automation/scripts/upgrade/auto-upgrade.sh --clear-failure\` を実行すると再開します"
  log_error "$reason"
  exit 1
}

if [[ -f "$failure_file" ]]; then
  log_warning "停止フラグがあるため何もしません: $(<"$failure_file")"
  log_warning "原因を確かめてから --clear-failure で解除します"
  exit 0
fi

desired="$(read_kubernetes_version)" || stop_with_failure "kubernetes-version.txt を読めません"

k8s_user="$(get_config kubernetes user "k8suser")"
control_plane_ip="$(get_config network control_plane_ip "192.168.122.10")"
ssh_key_path="$(get_config kubernetes ssh_key_path "")"
ssh_cmd=(ssh -o StrictHostKeyChecking=no -o BatchMode=yes -o LogLevel=ERROR -o ConnectTimeout=15)
[[ -z "$ssh_key_path" ]] || ssh_cmd+=(-i "$ssh_key_path")
ssh_cmd+=("${k8s_user}@${control_plane_ip}")

remote_kubectl() {
  "${ssh_cmd[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl $*"
}

# 接続できないのは一時的な障害とみなし、次回に再判定する（クラスタの停止は監視が通知する）
if ! version_json="$(remote_kubectl get --raw /version)"; then
  log_warning "control-plane の API に接続できません。次回に再判定します"
  exit 0
fi
server_version="$(sed -n 's/.*"gitVersion": *"\([^"]*\)".*/\1/p' <<<"$version_json")"
node_versions="$(remote_kubectl "get nodes -o jsonpath='{range .items[*]}{.metadata.name} {.status.nodeInfo.kubeletVersion}{\"\\n\"}{end}'")"

if [[ ! "$server_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || [[ -z "$node_versions" ]]; then
  log_warning "クラスタの版を読めません（apiserver='${server_version}'）。次回に再判定します"
  exit 0
fi

log_status "目標: ${desired} / apiserver: ${server_version}"
log_status "ノード: $(tr '\n' ' ' <<<"$node_versions")"

mixed_nodes="$(awk -v v="$server_version" '$2 != v' <<<"$node_versions")"
if [[ -n "$mixed_nodes" ]]; then
  stop_with_failure "apiserver (${server_version}) とノードの版が揃っていません: $(tr '\n' ' ' <<<"$mixed_nodes")"
fi

if [[ "$desired" == "$server_version" ]]; then
  rm -f "$gate_notice_file"
  log_status "目標の版です。何もしません"
  exit 0
fi

newest="$(printf '%s\n%s\n' "$desired" "$server_version" | sort -V | tail -n 1)"
if [[ "$newest" != "$desired" ]]; then
  stop_with_failure "目標 ${desired} が現在の ${server_version} より古いです（kubeadm は版を戻せません）"
fi

current_minor="$(kubernetes_minor "$server_version")"
desired_minor="$(kubernetes_minor "$desired")"
if [[ "${current_minor%%.*}" != "${desired_minor%%.*}" ]] || (( ${desired_minor#*.} > ${current_minor#*.} + 1 )); then
  stop_with_failure "${server_version} から ${desired} へは minor を飛ばします。1 つずつ上げてください"
fi

# pkgs.k8s.io への公開は GitHub の release より遅れることがある
packages_url="https://pkgs.k8s.io/core:/stable:/v${desired_minor}/deb/Packages"
if ! packages="$(curl -fsSL -m 60 "$packages_url")"; then
  log_warning "パッケージ一覧を取得できません（${packages_url}）。次回に再判定します"
  exit 0
fi
for package in kubeadm kubelet kubectl; do
  if ! awk -v p="$package" -v v="${desired#v}-" '
      $1 == "Package:" { name = $2 }
      $1 == "Version:" && name == p && index($2, v) == 1 { found = 1 }
      END { exit found ? 0 : 1 }' <<<"$packages"; then
    log_status "${package} ${desired} はまだ pkgs.k8s.io にありません。次回に再判定します"
    exit 0
  fi
done

if [[ "$mode" == "dry-run" ]]; then
  log_status "[dry-run] ${server_version} -> ${desired} の upgrade-safe を実行します"
  exit 0
fi

# 事前ゲートは一時的に落ちうる（Renovate の反映中など）。止めずに次回へ回し、通知は版ごとに 1 回
if ! gate_output="$("$UPGRADE_DIR/upgrade-gate-check.sh" --phase pre 2>&1)"; then
  printf '%s\n' "$gate_output"
  if [[ "$(cat "$gate_notice_file" 2>/dev/null)" != "$desired" ]]; then
    notify "${server_version} -> ${desired} を待っています。事前ゲートが通りません（通るまで 1 時間ごとに再判定）
$(grep -E '^ *[^ ]+/[^ ]+ status=|sync=|NotReady' <<<"$gate_output" | head -n 10 || true)"
    printf '%s\n' "$desired" >"$gate_notice_file"
  fi
  exit 0
fi
rm -f "$gate_notice_file"

notify "${server_version} -> ${desired} を開始します。API サーバーと worker の Pod が一時的に止まります"
started_at="$(date +%s)"
if NON_INTERACTIVE=true "$UPGRADE_AUTOMATION_DIR/scripts/run.sh" upgrade-safe; then
  elapsed=$(( ($(date +%s) - started_at) / 60 ))
  notify "${server_version} -> ${desired} が完了しました（${elapsed} 分）。URL、Harbor の pull、DB を使うアプリを確かめてください"
else
  stop_with_failure "${server_version} -> ${desired} の upgrade-safe が失敗しました。ログ: journalctl -u k8s-auto-upgrade、automation/run.log"
fi
