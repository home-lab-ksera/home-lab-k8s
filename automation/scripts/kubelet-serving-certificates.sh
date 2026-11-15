#!/usr/bin/env bash
# 未承認の kubelet serving CSR を1回だけ承認し、全ノードの配信証明書を検証する。既存ノードの設定変更は行わない。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [[ -f "$SCRIPT_DIR/settings-loader.sh" ]]; then
  # shellcheck source=settings-loader.sh
  source "$SCRIPT_DIR/settings-loader.sh" load 2>/dev/null || true
fi
mode="${1:---prepare}"
if [[ "$mode" != --prepare && "$mode" != --verify-only ]]; then
  echo 'Usage: kubelet-serving-certificates.sh [--prepare|--verify-only]' >&2
  exit 1
fi
host="${K8S_USER:-k8suser}@${K8S_CONTROL_PLANE_IP:-192.168.122.10}"
ssh_options=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no)
if [[ -n "${K8S_SSH_KEY:-}" ]]; then
  ssh_options+=(-i "$K8S_SSH_KEY")
fi
# VM再構築時の接続方針は既存bootstrapと揃える。
ssh_cmd=(ssh -T "${ssh_options[@]}" "$host")
remote_tmp=''
cleanup() {
  if [[ -n "$remote_tmp" ]]; then "${ssh_cmd[@]}" "rm -rf -- '$remote_tmp'" || true; fi
}
trap cleanup EXIT
remote_tmp="$("${ssh_cmd[@]}" 'mktemp -d /tmp/kubelet-certs.XXXXXXXXXX')"
[[ "$remote_tmp" =~ ^/tmp/kubelet-certs\.[a-zA-Z0-9]+$ ]] || { remote_tmp=''; exit 1; }
scp "${ssh_options[@]}" "$SCRIPT_DIR/kubelet-serving-nodes.json" "$SCRIPT_DIR/verify-kubelet-serving.py" \
  "$SCRIPT_DIR/approve-kubelet-serving.py" "$host:$remote_tmp/"
if [[ "$mode" == --prepare ]]; then
  # 台帳のノード自身が出した未承認の serving CSR だけを承認する。常駐の承認処理は置かない。
  "${ssh_cmd[@]}" "set -o pipefail; kubectl get csr -o json | python3 '$remote_tmp/approve-kubelet-serving.py' '$remote_tmp/kubelet-serving-nodes.json' | xargs -r kubectl certificate approve"
fi
"${ssh_cmd[@]}" "python3 '$remote_tmp/verify-kubelet-serving.py' '$remote_tmp/kubelet-serving-nodes.json'"
