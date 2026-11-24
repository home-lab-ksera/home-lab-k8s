#!/bin/bash
# SSH接続用共通関数
# 重複するSSH接続パターンを統一

# settings.toml から設定読み込み（呼び出し側の SCRIPT_DIR は上書きしない）
_COMMON_SSH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! declare -F get_config >/dev/null && [[ -f "$_COMMON_SSH_DIR/settings-loader.sh" ]]; then
    source "$_COMMON_SSH_DIR/settings-loader.sh" load 2>/dev/null || true
fi

# デフォルト設定
DEFAULT_SSH_USER="${K8S_USER:-k8suser}"
DEFAULT_SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=yes -o LogLevel=ERROR -o ConnectTimeout=10"

# 標準の SSH オプションを配列変数に設定する。
# 使い方: k8s_ssh_opts <配列変数名> <鍵ファイル(空なら指定しない)> [追加オプション...]
k8s_ssh_opts() {
    local -n _k8s_ssh_opts_out="$1"
    local key="${2:-}"
    _k8s_ssh_opts_out=(-o StrictHostKeyChecking=no -o BatchMode=yes -o LogLevel=ERROR "${@:3}")
    if [[ -n "$key" ]]; then
        _k8s_ssh_opts_out+=(-i "$key")
    fi
}

# コントロールプレーンへのSSH
k8s_ssh_control() {
    local cmd="${1:-}"
    local host="${CONTROL_PLANE_IP:-192.168.122.10}"
    
    if [[ -z "$cmd" ]]; then
        ssh $DEFAULT_SSH_OPTS ${DEFAULT_SSH_USER}@${host}
    else
        ssh $DEFAULT_SSH_OPTS ${DEFAULT_SSH_USER}@${host} "$cmd"
    fi
}

# ワーカーノードへのSSH
k8s_ssh_worker() {
    local worker_num="${1:-2}"
    local cmd="${2:-}"
    local host
    
    case $worker_num in
        2) host="${WORKER_2_IP:-192.168.122.12}" ;;
        *) echo "Invalid worker number: $worker_num" >&2; return 1 ;;
    esac
    
    if [[ -z "$cmd" ]]; then
        ssh $DEFAULT_SSH_OPTS ${DEFAULT_SSH_USER}@${host}
    else
        ssh $DEFAULT_SSH_OPTS ${DEFAULT_SSH_USER}@${host} "$cmd"
    fi
}

# 全ノードでコマンド実行
k8s_ssh_all_nodes() {
    local cmd="$1"
    local nodes=(
        "${CONTROL_PLANE_IP:-192.168.122.10}"
        "${WORKER_2_IP:-192.168.122.12}"
    )
    
    for node in "${nodes[@]}"; do
        echo "=== Executing on $node ==="
        ssh $DEFAULT_SSH_OPTS ${DEFAULT_SSH_USER}@${node} "$cmd" || {
            echo "Failed to execute on $node" >&2
            return 1
        }
    done
}

# SSH接続テスト
k8s_ssh_test() {
    local host="${1:-${CONTROL_PLANE_IP:-192.168.122.10}}"
    ssh $DEFAULT_SSH_OPTS -q ${DEFAULT_SSH_USER}@${host} exit
}

# SCPでファイル転送
k8s_scp() {
    local src="$1"
    local dst="$2"
    local host="${3:-${CONTROL_PLANE_IP:-192.168.122.10}}"
    
    scp $DEFAULT_SSH_OPTS "$src" ${DEFAULT_SSH_USER}@${host}:"$dst"
}

# ファイルを全ノードに配布
k8s_scp_all_nodes() {
    local src="$1"
    local dst="$2"
    local nodes=(
        "${CONTROL_PLANE_IP:-192.168.122.10}"
        "${WORKER_2_IP:-192.168.122.12}"
    )
    
    for node in "${nodes[@]}"; do
        echo "Copying to $node..."
        scp $DEFAULT_SSH_OPTS "$src" ${DEFAULT_SSH_USER}@${node}:"$dst" || {
            echo "Failed to copy to $node" >&2
            return 1
        }
    done
}

# kubectl実行（コントロールプレーン経由）
k8s_kubectl() {
    k8s_ssh_control "kubectl $*"
}

# kubeadmトークン取得
k8s_get_join_command() {
    k8s_ssh_control "sudo kubeadm token create --print-join-command"
}