#!/usr/bin/env bash
# 0 からの再構築訓練。GitHub-hosted の使い捨て runner で、本番と同じ phase1 → phase2 → bootstrap を通しで実行する。
# phase2 は名前に k8s を含む VM を削除するため、runner 以外では絶対に実行しない。
# 本番の Secret は渡さない。ESO の token はダミーで、Secret に依存しない Application の収束までを合格条件にする。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DIAG_DIR="${RUNNER_TEMP:-/tmp}/rebuild-drill"
CONTROL_PLANE=192.168.122.10

if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_ENVIRONMENT:-}" != github-hosted ]]; then
  echo 'GitHub-hosted runner 専用です（phase2 は既存の VM を削除します）' >&2
  exit 1
fi

# Secret が無くても Synced / Healthy になるべき Application。
# Secret（Pulumi ESC）、Harbor のイメージ、Tailscale、Cloudflare に依存するものは含めない。
REQUIRED_HEALTHY=(
  argocd-projects
  argocd-core
  local-path-provisioner
  nfs-subdir-external-provisioner
  core
  kube-network-policies
  metallb
  metrics-server
  metallb-config
  gateway-api
  nginx-gateway-fabric
  cert-manager
  external-secrets-operator
  reloader
)

SSH_CP=(ssh -T -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR
  -o ConnectTimeout=10 "k8suser@${CONTROL_PLANE}")

ssh_cp() {
  "${SSH_CP[@]}" "$@"
}

# リポジトリにある Application の名前（bootstrap-root を含む）。
expected_applications() {
  find "$ROOT_DIR/manifests/bootstrap" -name '*.yaml' -print0 \
    | xargs -0 awk '/^kind: Application$/ { k = 1; next } k && /^  name:/ { print $2; k = 0 }' \
    | sort -u
}

application_table() {
  ssh_cp "kubectl get applications.argoproj.io -n argocd -o jsonpath='{range .items[*]}{.metadata.name}{\"\\t\"}{.status.sync.status}{\"\\t\"}{.status.health.status}{\"\\n\"}{end}'" \
    | sort
}

case "${1:-}" in
  preflight)
    mkdir -p "$DIAG_DIR"
    test -c /dev/kvm || { echo '/dev/kvm がありません。ネスト仮想化できる runner が必要です' >&2; exit 1; }
    # VM のディスクとイメージ置き場を空ける（runner に同梱の大きな SDK を消す）。
    sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc /opt/hostedtoolcache/CodeQL /usr/local/share/boost
    awk '/MemTotal/ { exit ($2 < 15 * 1024 * 1024) }' /proc/meminfo \
      || { echo 'メモリが 15GiB 未満です' >&2; exit 1; }
    test "$(df -Pk / | awk 'NR == 2 { print $4 }')" -ge $((30 * 1024 * 1024)) \
      || { echo 'ディスクの空きが 30GiB 未満です' >&2; df -h /; exit 1; }
    { uname -a; nproc; free -m; df -h /; } | tee "$DIAG_DIR/preflight.txt"
    ;;

  runner-prep)
    # 本番ホストにあって runner に無いものだけを用意する。
    # 1. NFS の共有。本番は setup-storage.sh で作るが、ディスクを操作するため訓練では使わない。
    sudo apt-get update -q
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q nfs-kernel-server
    sudo mkdir -p /mnt/k8s-storage/nfs-share
    sudo chmod 777 /mnt/k8s-storage/nfs-share
    echo '/mnt/k8s-storage/nfs-share 192.168.122.0/24(rw,sync,no_subtree_check,no_root_squash)' | sudo tee /etc/exports
    sudo systemctl restart nfs-kernel-server
    sudo exportfs -v
    # 2. Ubuntu 24.04 runner の virt-aa-helper は backing image の許可を作らないことがある。
    #    AppArmor は有効のまま、訓練の VM イメージだけを許可する。
    sudo mkdir -p /etc/apparmor.d/abstractions/libvirt-qemu.d
    sudo tee /etc/apparmor.d/abstractions/libvirt-qemu.d/rebuild-drill >/dev/null <<'APPARMOR'
/var/lib/libvirt/images/ubuntu-base-*.img rk,
/var/lib/libvirt/images/k8s-*.qcow2 rwk,
/var/lib/libvirt/images/k8s-*-init-*.iso rk,
APPARMOR
    ;;

  phase1)
    "$ROOT_DIR/automation/scripts/run.sh" phase1
    ;;

  phase2)
    # phase1 で追加した libvirt グループは、再ログインの代わりに sg で反映する。
    sg libvirt -c "\"$ROOT_DIR/automation/scripts/run.sh\" phase2"
    ssh_cp 'kubectl wait --for=condition=Ready nodes --all --timeout=300s && kubectl get nodes -o wide'
    test "$(ssh_cp 'kubectl get nodes --no-headers | wc -l')" -eq 2
    ;;

  bootstrap)
    PULUMI_ACCESS_TOKEN=rebuild-drill-without-secrets "$ROOT_DIR/automation/scripts/run.sh" bootstrap
    ;;

  converge)
    timeout_minutes="${CONVERGE_TIMEOUT_MINUTES:-45}"
    mapfile -t expected < <(expected_applications)
    deadline=$((SECONDS + timeout_minutes * 60))
    while :; do
      table="$(application_table 2>/dev/null || true)"
      missing=()
      for app in "${expected[@]}"; do
        grep -q "^${app}"$'\t' <<<"$table" || missing+=("$app")
      done
      unhealthy=()
      for app in "${REQUIRED_HEALTHY[@]}"; do
        grep -q "^${app}"$'\t'"Synced"$'\t'"Healthy$" <<<"$table" || unhealthy+=("$app")
      done
      if [[ ${#missing[@]} -eq 0 && ${#unhealthy[@]} -eq 0 ]]; then
        break
      fi
      if (( SECONDS >= deadline )); then
        echo "::error::${timeout_minutes} 分以内に収束しませんでした"
        [[ ${#missing[@]} -gt 0 ]] && echo "作られていない Application: ${missing[*]}"
        [[ ${#unhealthy[@]} -gt 0 ]] && echo "Synced / Healthy でない Application: ${unhealthy[*]}"
        printf '%s\n' "$table" | column -t
        exit 1
      fi
      echo "待機中: 未作成 ${#missing[@]} / 未収束 ${#unhealthy[@]}（残り $(( (deadline - SECONDS) / 60 )) 分）"
      sleep 30
    done
    echo "全 ${#expected[@]} 個の Application が作られ、必須の ${#REQUIRED_HEALTHY[@]} 個が Synced / Healthy です"
    printf '%s\n' "$table" | column -t | tee "$DIAG_DIR/applications.txt"
    {
      echo "### 再構築訓練"
      echo
      echo "全 ${#expected[@]} 個の Application が作られ、Secret に依存しない ${#REQUIRED_HEALTHY[@]} 個が Synced / Healthy になりました。"
      echo
      echo '| Application | Sync | Health |'
      echo '|---|---|---|'
      awk -F '\t' '{ printf "| %s | %s | %s |\n", $1, $2, $3 }' <<<"$table"
    } >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
    ;;

  collect)
    mkdir -p "$DIAG_DIR"
    sudo virsh list --all > "$DIAG_DIR/vms.txt" 2>&1 || true
    free -m > "$DIAG_DIR/runner-memory.txt" 2>&1 || true
    df -h / > "$DIAG_DIR/runner-disk.txt" 2>&1 || true
    # join token や Secret を含みうる state・全ログは保存しない。
    timeout 60 "${SSH_CP[@]}" 'kubectl get nodes -o wide; echo; kubectl top nodes; echo; kubectl get applications.argoproj.io -n argocd; echo; kubectl get pods -A -o wide' \
      > "$DIAG_DIR/kubernetes.txt" 2>&1 || true
    timeout 60 "${SSH_CP[@]}" 'kubectl get events -A --sort-by=.lastTimestamp | tail -n 300' \
      > "$DIAG_DIR/events.txt" 2>&1 || true
    ;;

  *)
    echo 'Usage: rebuild-drill.sh preflight|runner-prep|phase1|phase2|bootstrap|converge|collect' >&2
    exit 1
    ;;
esac
