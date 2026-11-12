#!/bin/bash

# 完全クリーンアップ＆デプロイスクリプト（修正版）
# ネットワーク問題とlibvirt権限問題を解決

set -euo pipefail

# スクリプトディレクトリ取得
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 共通色設定スクリプトを読み込み
source "$SCRIPT_DIR/../scripts/common-logging.sh"

log_status "=== 完全クリーンアップ開始 ==="

ALLOW_WEAK_HOST_SECURITY="${ALLOW_WEAK_HOST_SECURITY:-false}"

# 0. 必要なパッケージの確認（expectは削除）
which jq >/dev/null 2>&1 || sudo apt-get install -y jq 2>/dev/null

# 1. 全てのVMを完全削除
for vm in $(sudo -n virsh list --all --name); do
    if [[ "$vm" == *"k8s"* ]]; then
        sudo -n virsh destroy "$vm" 2>/dev/null || true
        sudo -n virsh undefine "$vm" --remove-all-storage 2>/dev/null || true
    fi
done

# 2. libvirt関連ファイル完全削除
sudo -n rm -f /etc/libvirt/qemu/k8s-*.xml
sudo -n rm -f /var/lib/libvirt/images/k8s-*
sudo -n rm -f /var/lib/libvirt/images/*-init-*.iso
sudo -n rm -f /var/lib/libvirt/images/ubuntu-base-*.img
sudo -n rm -f /var/lib/libvirt/boot/k8s-*

# 3. Terraform状態完全削除
rm -rf .terraform/
rm -f terraform.tfstate*
rm -f tfplan

# 4. ホストセキュリティ低下設定（明示オプトイン時のみ）
if [[ "$ALLOW_WEAK_HOST_SECURITY" == "true" ]]; then
    log_warning "ALLOW_WEAK_HOST_SECURITY=true のため、ホスト保護を弱める設定を適用します"

    if systemctl is-active --quiet apparmor; then
        sudo -n systemctl stop apparmor 2>/dev/null
        sudo -n systemctl disable apparmor 2>/dev/null
    else
        log_debug "AppArmorは既に無効化されています"
    fi

    if command -v aa-disable >/dev/null 2>&1; then
        sudo -n aa-disable /usr/sbin/libvirtd 2>/dev/null || true
        sudo -n aa-disable /usr/lib/libvirt/virt-aa-helper 2>/dev/null || true
        log_debug "libvirt関連AppArmorプロファイルを無効化"
    fi
else
    log_status "安全モード: AppArmor/aa-disable の変更をスキップします"
fi

# 5. libvirt権限問題の根本修正
log_status "libvirt権限設定を修正中..."

# qemu.confのセキュリティ設定
if [[ "$ALLOW_WEAK_HOST_SECURITY" == "true" ]]; then
    if ! grep -q '^security_driver = "none"' /etc/libvirt/qemu.conf; then
        echo 'security_driver = "none"' | sudo -n tee -a /etc/libvirt/qemu.conf
    fi
else
    log_status "安全モード: security_driver=none の追記をスキップします"
fi

# ユーザー・グループ設定確認
sudo -n sed -i 's/^user = .*/user = "libvirt-qemu"/' /etc/libvirt/qemu.conf
sudo -n sed -i 's/^group = .*/group = "kvm"/' /etc/libvirt/qemu.conf

# libvirtプールの権限設定
sudo -n virsh pool-destroy default 2>/dev/null || true
sudo -n virsh pool-undefine default 2>/dev/null || true

cat > /tmp/default-pool.xml << 'EOF'
<pool type='dir'>
  <name>default</name>
  <target>
    <path>/var/lib/libvirt/images</path>
    <permissions>
      <mode>0755</mode>
      <owner>64055</owner>
      <group>108</group>
    </permissions>
  </target>
</pool>
EOF

sudo -n virsh pool-define /tmp/default-pool.xml
sudo -n virsh pool-start default
sudo -n virsh pool-autostart default
rm -f /tmp/default-pool.xml

# ディレクトリ権限修正
sudo -n chown -R libvirt-qemu:kvm /var/lib/libvirt/images/
sudo -n chmod 755 /var/lib/libvirt/images/

# 5. libvirtd完全再起動
log_status "libvirtdを再起動中..."
sudo -n systemctl stop libvirtd
sudo -n systemctl stop virtlogd
sleep 2
sudo -n systemctl start virtlogd
sudo -n systemctl start libvirtd
sleep 3

# 6. デフォルトネットワーク確認・修正
log_status "libvirtネットワークを確認中..."
if ! sudo -n virsh net-list | grep -q "default.*active"; then
    sudo -n virsh net-start default
    sudo -n virsh net-autostart default
fi

log_status "=== 新しい設計でデプロイ開始 ==="

# 7. SSH鍵確認
if [[ ! -f ~/.ssh/id_rsa ]]; then
    log_status "SSH鍵を生成中..."
    ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N ""
fi

# 8. terraform.tfvars作成
SSH_PUB_KEY=$(cat ~/.ssh/id_rsa.pub)
cat > terraform.tfvars << EOF
vm_user = "k8suser"
ssh_public_key = "$SSH_PUB_KEY"
control_plane_ip = "192.168.122.10"
network_gateway = "192.168.122.1"
EOF

# 9. main.tfのネットワーク設定修正
log_status "main.tfのネットワーク設定を修正中..."
if [[ -f main.tf ]]; then
    # wait_for_lease問題を修正
    sed -i 's/wait_for_lease = true/wait_for_lease = false/g' main.tf
    log_debug "wait_for_lease = false に変更"
else
    log_error "main.tf が見つかりません"
    exit 1
fi

# 10. Terraform初期化＆プラン
log_status "Terraformを初期化中..."
terraform init

log_status "Terraformプランを作成中..."
terraform plan -out=tfplan

# 11. デプロイ実行
log_status "VM構築を開始中..."
terraform apply tfplan

# 12. デプロイ後の確認・修正
if [[ $? -eq 0 ]]; then
    log_status "=== 初期デプロイ完了 ==="
    
    # VMの作成確認
    log_status "VM状態を確認中..."
    sudo -n virsh list --all
    
    # 権限問題が発生している可能性があるので再修正
    log_status "作成後の権限を修正中..."
    sudo -n chown -R libvirt-qemu:kvm /var/lib/libvirt/images/
    sudo -n find /var/lib/libvirt/images/ -name "*.img" -exec chmod 644 {} \; 2>/dev/null || true
    sudo -n find /var/lib/libvirt/images/ -name "*.qcow2" -exec chmod 644 {} \; 2>/dev/null || true
    
    # VM起動確認
    sleep 10
    log_status "VM起動状況を確認中..."
    for i in {1..6}; do
        VM_COUNT=$(sudo -n virsh list --state-running | grep k8s | wc -l)
        if [[ $VM_COUNT -eq 3 ]]; then
            log_status "全VM起動完了"
            break
        else
            log_debug "VM起動待機中... ($i/6) 起動中: $VM_COUNT/3"
            sleep 10
        fi
    done
    
    # ネットワーク確認
    log_status "ネットワーク設定を確認中..."
    sleep 20
    sudo -n virsh net-dhcp-leases default
    
    # VM起動完了待機とネットワーク確認
    log_status "VM起動完了とネットワーク設定を確認中..."
    log_debug "cloud-init/network-config.yamlで既にens3設定済みのため、expectによる修正は不要"
    
    # cloud-initの完了を待機
    sleep 60
    
    # SSH接続テスト
    log_status "SSH接続テスト中..."
    sleep 20  # ネットワーク設定の反映を待機
    
    for ip in 192.168.122.10 192.168.122.12; do
        if ping -c 3 "$ip" >/dev/null 2>&1; then
            log_status "✓ $ip への接続OK"
            # SSH接続テスト
            if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 k8suser@"$ip" "echo 'SSH接続成功'" 2>/dev/null; then
                log_status "✓ $ip へのSSH接続OK"
            else
                log_warning "⚠ $ip へのSSH接続失敗（cloud-init実行中またはネットワーク設定要確認）"
            fi
        else
            log_warning "⚠ $ip への接続失敗（ネットワーク設定要確認）"
        fi
    done
    
    log_status "=== デプロイ完了 ==="
    echo ""
    echo "=== VM接続情報 ==="
    echo "Control Plane: ssh k8suser@192.168.122.10"
    echo "Worker Node 2: ssh k8suser@192.168.122.12"
    echo ""
    echo "=== Phase 2&3統合デプロイ完了 ==="
    echo "VM構築とKubernetesクラスター構築が統合されました。"
    echo "Terraformによりkubeadmクラスターが自動構築されています。"
    echo ""
    echo "=== 確認コマンド ==="
    echo "VM状態: sudo -n virsh list --all"
    echo "クラスター状態: ssh k8suser@192.168.122.10 'kubectl get nodes -o wide'"
    echo "Pod状態: ssh k8suser@192.168.122.10 'kubectl get pods --all-namespaces'"
    echo "kubeconfigコピー: scp k8suser@192.168.122.10:/home/k8suser/.kube/config ~/.kube/config-k8s-cluster"
    echo "ネットワーク: sudo -n virsh net-dhcp-leases default"
    
else
    log_error "=== デプロイ失敗 ==="
    log_error "デバッグ情報:"
    log_debug "VM状態:"
    sudo -n virsh list --all
    log_debug "ネットワーク状態:"
    sudo -n virsh net-list --all
    log_debug "DHCPリース:"
    sudo -n virsh net-dhcp-leases default
    log_debug "ファイル権限:"
    ls -la /var/lib/libvirt/images/ | head -10
    exit 1
fi
