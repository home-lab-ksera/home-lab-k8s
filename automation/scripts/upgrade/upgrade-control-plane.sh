#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOMATION_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 共通ログ出力
# shellcheck source=automation/scripts/common-logging.sh
source "$AUTOMATION_DIR/scripts/common-logging.sh"

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

# shellcheck source=automation/scripts/common-ssh.sh
source "$AUTOMATION_DIR/scripts/common-ssh.sh"
ssh_opts=()
k8s_ssh_opts ssh_opts "$ssh_key_path"

ssh_cmd=(ssh "${ssh_opts[@]}" "${k8s_user}@${control_plane_ip}")

log_status "=== Upgrade Control Plane ==="
log_status "対象バージョン: ${target_version}"
log_status "APTチャネル: ${apt_channel}"

"${ssh_cmd[@]}" "sudo bash -c 'set -euo pipefail; echo deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/${apt_channel}/deb/ / > /etc/apt/sources.list.d/kubernetes.list'"
"${ssh_cmd[@]}" "sudo apt-get update -y"

"${ssh_cmd[@]}" "sudo apt-mark unhold kubeadm kubelet kubectl"
"${ssh_cmd[@]}" "sudo bash -c 'set -euo pipefail; target=\"${target_version#v}\"; pkg=\"\$(apt-cache madison kubeadm | awk -F\"|\" -v t=\"${target_version#v}\" '\''{gsub(/ /, \"\", \$2); if (index(\$2, t \"-\") == 1 && first == \"\") {first = \$2}} END {print first}'\'')\"; if [[ -z \"\$pkg\" ]]; then echo \"kubeadm \$target が見つかりません\" >&2; exit 1; fi; apt-get install -y kubeadm=\"\$pkg\"'"

log_status "kubeadm upgrade apply 実行中..."
"${ssh_cmd[@]}" "sudo KUBECONFIG=/etc/kubernetes/admin.conf kubeadm upgrade apply ${target_version} -y"

"${ssh_cmd[@]}" "sudo bash -c 'set -euo pipefail; target=\"${target_version#v}\"; pkg=\"\$(apt-cache madison kubelet | awk -F\"|\" -v t=\"${target_version#v}\" '\''{gsub(/ /, \"\", \$2); if (index(\$2, t \"-\") == 1 && first == \"\") {first = \$2}} END {print first}'\'')\"; if [[ -z \"\$pkg\" ]]; then echo \"kubelet \$target が見つかりません\" >&2; exit 1; fi; apt-get install -y kubelet=\"\$pkg\" kubectl=\"\$pkg\"; systemctl daemon-reload; systemctl restart kubelet'"

# unattended-upgrades や手作業の apt upgrade で版が動かないよう固定する
"${ssh_cmd[@]}" "sudo apt-mark hold kubeadm kubelet kubectl"

log_status "Upgrade Control Plane 完了"
