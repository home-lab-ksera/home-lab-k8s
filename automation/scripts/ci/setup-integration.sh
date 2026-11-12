#!/usr/bin/env bash
# GitHub-hosted の使い捨て runner 専用。既存ホストでは実行しない。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_ENVIRONMENT:-}" != github-hosted ]]; then
  echo 'GitHub-hosted runner 専用です' >&2
  exit 1
fi
: "${RUNNER_TEMP:?}"
: "${SETUP_CI_DIR:?}"
if [[ "$SETUP_CI_DIR" != "$RUNNER_TEMP/setup-integration" ]]; then
  echo 'SETUP_CI_DIR は runner の一時ディレクトリを指定してください' >&2
  exit 1
fi

ssh_vm() {
  local ip="$1"
  shift
  ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=5 -i "$SETUP_CI_DIR/id_ed25519" "k8suser@$ip" "$@"
}

case "${1:-}" in
  preflight)
    mkdir -p "$SETUP_CI_DIR/diagnostics"
    {
      uname -a
      free -m
      df -h "$RUNNER_TEMP"
      test -c /dev/kvm || { echo '/dev/kvm がありません。ネスト仮想化可能な runner が必要です'; exit 1; }
      sudo python3 - <<'PY'
import fcntl
import os
fd = os.open('/dev/kvm', os.O_RDWR)
assert fcntl.ioctl(fd, 0xAE00, 0) == 12, 'KVM API unavailable'
vm = fcntl.ioctl(fd, 0xAE01, 0)
os.close(vm)
os.close(fd)
PY
      # VM 合計 7GiB とホスト分を確保する。
      awk '/MemTotal/ {exit ($2 < 12 * 1024 * 1024)}' /proc/meminfo
      test "$(df -Pk "$RUNNER_TEMP" | awk 'NR==2 {print $4}')" -ge 10485760
    } 2>&1 | tee "$SETUP_CI_DIR/diagnostics/preflight.log"
    ;;
  prepare)
    sudo apt-get update
    sudo apt-get install -y qemu-kvm libvirt-daemon-system libvirt-clients genisoimage xsltproc
    # Ubuntu 24.04 の virt-aa-helper が backing image の許可を生成しない場合に備える。
    # 使い捨てCIのイメージ名だけを許可し、AppArmor自体は有効のままにする。
    sudo mkdir -p /etc/apparmor.d/abstractions/libvirt-qemu.d
    sudo tee /etc/apparmor.d/abstractions/libvirt-qemu.d/setup-integration >/dev/null <<'APPARMOR'
/var/lib/libvirt/images/ubuntu-base-*.img rk,
/var/lib/libvirt/images/k8s-*.qcow2 rwk,
/var/lib/libvirt/images/k8s-*-init-*.iso rk,
APPARMOR
    sudo systemctl start libvirtd
    # 後続のTerraformはsgで新しい補助グループを反映して実行する。
    sudo usermod -aG libvirt "$(id -un)"
    sudo virsh net-info default >/dev/null 2>&1 || sudo virsh net-define /usr/share/libvirt/networks/default.xml
    if ! sudo virsh net-info default | awk '/^Active:/ {exit ($2 != "yes")}'; then
      sudo virsh net-start default
    fi
    if ! sudo virsh pool-info default >/dev/null 2>&1; then
      sudo mkdir -p /var/lib/libvirt/images
      sudo virsh pool-define-as default dir --target /var/lib/libvirt/images
    fi
    if ! sudo virsh pool-info default | awk '/^State:/ {exit ($2 != "running")}'; then
      sudo virsh pool-start default
    fi
    mkdir -p "$SETUP_CI_DIR/infrastructure"
    cp "$ROOT_DIR"/automation/infrastructure/*.tf "$SETUP_CI_DIR/infrastructure/"
    cp -r "$ROOT_DIR/automation/infrastructure/cloud-init" "$SETUP_CI_DIR/infrastructure/"
    # main.tf が file() / provisioner で参照する同梱ファイル。
    cp "$ROOT_DIR/automation/infrastructure/libvirt-domain.xsl" "$SETUP_CI_DIR/infrastructure/"
    cp -r "$ROOT_DIR/automation/infrastructure/apiserver-security" "$SETUP_CI_DIR/infrastructure/"
    ssh-keygen -q -t ed25519 -N '' -f "$SETUP_CI_DIR/id_ed25519"
    python3 - <<'PY'
import json
import os
from pathlib import Path
base = Path(os.environ['SETUP_CI_DIR'])
config = {
    'ssh_public_key': (base / 'id_ed25519.pub').read_text().strip(),
    'ssh_private_key_path': str(base / 'id_ed25519'),
    'control_plane_memory': 3072, 'control_plane_vcpu': 2,
    'workers': {'k8s-worker2': {'ip': '192.168.122.12', 'memory': 2048, 'vcpu': 2}},
    'enable_nfs_mount': False,
}
(base / 'infrastructure/ci.auto.tfvars.json').write_text(json.dumps(config))
PY
    ;;
  certificates)
    K8S_USER=k8suser K8S_CONTROL_PLANE_IP=192.168.122.10 K8S_SSH_KEY="$SETUP_CI_DIR/id_ed25519" \
      bash "$ROOT_DIR/automation/scripts/kubelet-serving-certificates.sh" --prepare
    ;;
  reapply)
    # 差分がある場合（終了コード2）も失敗。意図しない再作成をapplyで隠さない。
    # stepの強制終了より先にプロセス群を停止し、stateロックをdestroyへ持ち越さない。
    sg libvirt -c 'exec timeout --kill-after=10s 80s terraform -chdir="$SETUP_CI_DIR/infrastructure" plan -detailed-exitcode -input=false -no-color'
    sg libvirt -c 'exec timeout --kill-after=10s 80s terraform -chdir="$SETUP_CI_DIR/infrastructure" apply -auto-approve -input=false -no-color'
    sg libvirt -c 'exec timeout --kill-after=10s 80s terraform -chdir="$SETUP_CI_DIR/infrastructure" plan -detailed-exitcode -input=false -no-color'
    ;;
  reboot)
    for ip in 192.168.122.12 192.168.122.10; do
      before="$(ssh_vm "$ip" 'cat /proc/sys/kernel/random/boot_id')"
      [[ "$before" =~ ^[0-9a-f-]{36}$ ]] || { echo "Invalid boot ID: $ip" >&2; exit 1; }
      # SSH切断を成功扱いせず、予約が成功した後にboot IDの変化を待つ。
      ssh_vm "$ip" 'sudo shutdown -r +1'
      deadline=$((SECONDS + 300))
      recovered=false
      while (( SECONDS < deadline )); do
        after="$(ssh_vm "$ip" 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null)" || after=''
        if [[ "$after" =~ ^[0-9a-f-]{36}$ && "$after" != "$before" ]]; then
          recovered=true
          break
        fi
        sleep 5
      done
      [[ "$recovered" == true ]] || { echo "Reboot timed out: $ip" >&2; exit 1; }
      echo "$ip rebooted: $before -> $after"
      ssh_vm "$ip" 'bash -se' <<'REMOTE'
set -euo pipefail
timeout 120 bash -c 'until systemctl is-active --quiet containerd && systemctl is-active --quiet kubelet; do sleep 3; done'
test "$(sysctl -n net.ipv4.ip_forward)" = 1
test "$(sysctl -n net.bridge.bridge-nf-call-iptables)" = 1
test -z "$(swapon --noheadings --show)"
REMOTE
    done
    # SSH復旧とAPI Server復旧は別に待つ。
    ssh_vm 192.168.122.10 'bash -se' <<'REMOTE'
set -euo pipefail
timeout 120 bash -c 'until kubectl get --raw=/readyz; do sleep 3; done'
REMOTE
    ;;
  verify)
    K8S_USER=k8suser K8S_CONTROL_PLANE_IP=192.168.122.10 K8S_SSH_KEY="$SETUP_CI_DIR/id_ed25519" \
      bash "$ROOT_DIR/automation/scripts/kubelet-serving-certificates.sh" --verify-only
    ssh_vm 192.168.122.10 'bash -se' <<'REMOTE'
set -euo pipefail
kubectl wait --for=condition=Ready nodes --all --timeout=240s
test "$(kubectl get nodes --no-headers | wc -l)" -eq 2
# 2VM 構成では control-plane も workload を受ける。taint と LB 除外 label が残っていないことを確認する。
test -z "$(kubectl get node k8s-control-plane -o jsonpath='{.spec.taints}')"
test -z "$(kubectl get node k8s-control-plane -o jsonpath='{.metadata.labels.node\.kubernetes\.io/exclude-from-external-load-balancers}')"
kubectl -n kube-system rollout status deployment/coredns --timeout=120s
kubectl create namespace setup-smoke --dry-run=client -o yaml | kubectl apply -f -
# 前回SucceededのPodを見て誤って成功しないよう、毎回新規に通信を実行する。
kubectl delete pod -n setup-smoke client --ignore-not-found --wait=true --timeout=60s
kubectl apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: server
  namespace: setup-smoke
  labels:
    app: setup-smoke
spec:
  nodeName: k8s-control-plane
  containers:
    - name: http
      image: busybox:1.37.0
      command: [sh, -ec, 'mkdir -p /www; echo setup-ok > /www/index.html; exec httpd -f -p 8080 -h /www']
---
apiVersion: v1
kind: Service
metadata:
  name: server
  namespace: setup-smoke
spec:
  selector:
    app: setup-smoke
  ports:
    - port: 8080
---
apiVersion: v1
kind: Pod
metadata:
  name: client
  namespace: setup-smoke
spec:
  nodeName: k8s-worker2
  restartPolicy: Never
  containers:
    - name: probe
      image: busybox:1.37.0
      command:
        - sh
        - -ec
        - |
          for attempt in $(seq 1 60); do
            if nslookup kubernetes.default.svc.cluster.local && \
              wget -T 5 -qO- http://server.setup-smoke.svc.cluster.local:8080 | grep -qx setup-ok; then
              exit 0
            fi
            sleep 2
          done
          exit 1
YAML
kubectl wait -n setup-smoke --for=jsonpath='{.status.phase}'=Succeeded pod/client --timeout=180s
kubectl logs -n setup-smoke client
kubectl get nodes -o wide
REMOTE
    ;;
  collect)
    mkdir -p "$SETUP_CI_DIR/diagnostics"
    sudo virsh list --all > "$SETUP_CI_DIR/diagnostics/vms.txt" 2>&1 || true
    # DAC権限とAppArmor拒否を区別できる最小限のホスト情報を残す。
    sudo bash -c 'ls -ld /var/lib/libvirt/images /var/lib/libvirt/images/*' \
      > "$SETUP_CI_DIR/diagnostics/image-permissions.txt" 2>&1 || true
    sudo journalctl -k --no-pager | awk '/apparmor="DENIED"/ && /libvirt/ && /\/var\/lib\/libvirt\/images\//' \
      > "$SETUP_CI_DIR/diagnostics/apparmor-denials.txt" || true
    for ip in 192.168.122.10 192.168.122.12; do
      # join token や認証情報を含む可能性がある全ログ・state は保存しない。
      timeout 25 ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=5 -i "$SETUP_CI_DIR/id_ed25519" "k8suser@$ip" \
        'cloud-init status --long; systemctl is-active containerd kubelet; free -m; df -h /' \
        > "$SETUP_CI_DIR/diagnostics/$ip.txt" 2>&1 || true
    done
    timeout 30 ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=5 -i "$SETUP_CI_DIR/id_ed25519" k8suser@192.168.122.10 \
      'kubectl get nodes -o wide; kubectl get pods -A -o wide; kubectl get events -A --sort-by=.lastTimestamp; kubectl logs -n setup-smoke client' \
      > "$SETUP_CI_DIR/diagnostics/kubernetes.txt" 2>&1 || true
    ;;
  *) echo 'Usage: setup-integration.sh preflight|prepare|certificates|verify|reapply|reboot|collect' >&2; exit 1 ;;
esac
