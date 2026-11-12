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

target_version="$(get_config upgrade target_version "")"
apt_channel="$(get_config upgrade apt_channel "")"
k8s_user="$(get_config kubernetes user "k8suser")"
control_plane_ip="$(get_config network control_plane_ip "192.168.122.10")"
ssh_key_path="$(get_config kubernetes ssh_key_path "")"

worker_2_ip="$(get_config network worker_2_ip "")"

if [[ -z "$target_version" ]]; then
  log_error "upgrade.target_version が未設定です (例: v1.33.7)"
  exit 1
fi

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

control_ssh=(ssh "${ssh_opts[@]}" "${k8s_user}@${control_plane_ip}")

worker_ips=()
if [[ -n "$worker_2_ip" ]]; then
  worker_ips+=("$worker_2_ip")
fi

if [[ ${#worker_ips[@]} -eq 0 ]]; then
  log_error "worker_2_ip が未設定です"
  exit 1
fi

log_status "=== Upgrade Workers ==="
log_status "対象バージョン: ${target_version}"
log_status "APTチャネル: ${apt_channel}"

for worker_ip in "${worker_ips[@]}"; do
  worker_ssh=(ssh "${ssh_opts[@]}" "${k8s_user}@${worker_ip}")

  node_name="$("${worker_ssh[@]}" hostname)"
  log_status "ノードアップグレード開始: ${node_name} (${worker_ip})"

  "${control_ssh[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl drain ${node_name} --ignore-daemonsets --delete-emptydir-data"

  "${worker_ssh[@]}" "sudo bash -c 'set -euo pipefail; echo deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/${apt_channel}/deb/ / > /etc/apt/sources.list.d/kubernetes.list'"
  "${worker_ssh[@]}" "sudo apt-get update -y"
  "${worker_ssh[@]}" "sudo apt-mark unhold kubeadm kubelet kubectl"
  "${worker_ssh[@]}" "sudo bash -c 'set -euo pipefail; target=\"${target_version#v}\"; pkg=\"\$(apt-cache madison kubeadm | awk -F\"|\" -v t=\"${target_version#v}\" '\''{gsub(/ /, \"\", \$2); if (index(\$2, t) == 1 && first == \"\") {first = \$2}} END {print first}'\'')\"; if [[ -z \"\$pkg\" ]]; then echo \"kubeadm \$target が見つかりません\" >&2; exit 1; fi; apt-get install -y kubeadm=\"\$pkg\"'"

  "${worker_ssh[@]}" "sudo kubeadm upgrade node"

  "${worker_ssh[@]}" "sudo bash -c 'set -euo pipefail; target=\"${target_version#v}\"; pkg=\"\$(apt-cache madison kubelet | awk -F\"|\" -v t=\"${target_version#v}\" '\''{gsub(/ /, \"\", \$2); if (index(\$2, t) == 1 && first == \"\") {first = \$2}} END {print first}'\'')\"; if [[ -z \"\$pkg\" ]]; then echo \"kubelet \$target が見つかりません\" >&2; exit 1; fi; apt-get install -y kubelet=\"\$pkg\" kubectl=\"\$pkg\"; systemctl daemon-reload; systemctl restart kubelet'"

  "${control_ssh[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubectl uncordon ${node_name}"

  log_status "ノードアップグレード完了: ${node_name}"
done

log_status "Upgrade Workers 完了"
