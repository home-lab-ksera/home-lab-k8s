#!/bin/bash

# Phase 1: 各種アプリケーションinstall
# Ubuntu 24.04 LTS ホストマシン準備スクリプト

set -euo pipefail

# 共通カラー定義を読み込み
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../scripts/common-logging.sh"

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   log_error "This script should not be run as root"
   exit 1
fi

log_status "Starting Phase 1: Host machine setup for k8s migration"

# 0. Clean up problematic repositories (Helm 403 error fix)
log_status "Cleaning up problematic APT repositories..."
if [ -f /etc/apt/sources.list.d/helm-stable-debian.list ]; then
    log_warning "Removing broken Helm repository (403 error fix)..."
    sudo rm -f /etc/apt/sources.list.d/helm-stable-debian.list
    sudo rm -f /usr/share/keyrings/helm.gpg
    log_status "✓ Removed problematic Helm repository"
fi

# 1. System update
log_status "Updating system packages..."
sudo apt update && sudo apt upgrade -y

# 2. Install virtualization packages
log_status "Installing QEMU/KVM and libvirt packages..."
sudo apt install -y \
    qemu-kvm \
    libvirt-daemon-system \
    libvirt-clients \
    bridge-utils \
    virtinst \
    virt-manager \
    virt-viewer \
    cpu-checker \
    genisoimage  # Terraform の libvirt provider が cloud-init の ISO を作るのに mkisofs を使う

# 3. Install development and automation tools
log_status "Installing development and automation tools..."
sudo apt install -y \
    git \
    curl \
    wget \
    unzip \
    jq \
    tree \
    htop \
    vim \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release

# 4. Install Terraform
log_status "Installing Terraform..."
# 既存のキーファイルを削除してから作成（上書き確認を回避）
sudo rm -f /usr/share/keyrings/hashicorp-archive-keyring.gpg
wget -O- https://apt.releases.hashicorp.com/gpg | \
    gpg --dearmor | \
    sudo tee /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] \
    https://apt.releases.hashicorp.com $(lsb_release -cs) main" | \
    sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update
sudo apt install -y terraform

# 5. Install Ansible
log_status "Installing Ansible..."
sudo apt install -y ansible

# 6. Install Docker (for building and testing)
log_status "Installing Docker..."
for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do sudo apt-get remove $pkg; done
sudo apt-get update || true  # Ignore repository errors
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update || true  # Ignore repository errors
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 7. Install kubectl
log_status "Installing kubectl..."
# 既存のキーファイルを削除してから作成（上書き確認を回避）
sudo rm -f /etc/apt/keyrings/kubernetes-apt-keyring.gpg
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo apt-get update || true  # Ignore repository errors
sudo apt-get install -y kubectl

# 8. Install helm
log_status "Installing Helm..."
# 古いHelmリポジトリファイルがあれば削除（403エラー対策）
if [ -f /etc/apt/sources.list.d/helm-stable-debian.list ]; then
    log_status "Removing old Helm repository configuration..."
    sudo rm -f /etc/apt/sources.list.d/helm-stable-debian.list
    sudo rm -f /usr/share/keyrings/helm.gpg
fi
# Helmの公式インストールスクリプトを使用（apt リポジトリの問題を回避）
if ! command -v helm &> /dev/null; then
    curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
    chmod 700 get_helm.sh
    ./get_helm.sh
    rm get_helm.sh
else
    log_status "Helm is already installed"
fi

# 9. Add user to required groups
log_status "Adding user to required groups..."
sudo usermod -aG libvirt $USER
sudo usermod -aG kvm $USER
sudo usermod -aG docker $USER

# 10. Check virtualization support
log_status "Checking virtualization support..."
if kvm-ok; then
    log_status "KVM virtualization is supported"
else
    log_warning "KVM virtualization may not be fully supported. Please check BIOS settings."
fi

# 11. Enable and start services
log_status "Enabling and starting required services..."
sudo systemctl enable libvirtd
sudo systemctl start libvirtd
sudo systemctl enable docker
sudo systemctl start docker

# 12. Verify installations
log_status "Verifying installations..."

# Check versions
echo "=== Installation Verification ==="
echo "Terraform version: $(terraform version -json | jq -r '.terraform_version')"
echo "Ansible version: $(ansible --version | head -1)"
echo "Docker version: $(docker --version)"
echo "kubectl version: $(kubectl version --client --short 2>/dev/null || echo 'kubectl installed')"
echo "Helm version: $(helm version --short)"

# Check services
echo ""
echo "=== Service Status ==="
systemctl is-active --quiet libvirtd && echo "libvirtd: Active" || echo "libvirtd: Inactive"
systemctl is-active --quiet docker && echo "docker: Active" || echo "docker: Inactive"

# Check group membership
echo ""
echo "=== Group Membership ==="
echo "Groups for user $USER: $(groups $USER)"

log_status "Phase 1 setup completed successfully!"
log_warning "Please log out and log back in (or run 'newgrp libvirt && newgrp docker') to refresh group membership."
log_status "After re-login, run storage setup only if needed: automation/host-setup/setup-storage.sh"

# Create next step reminder
cat > /tmp/next-steps.txt << EOF
Phase 1 completed successfully!

Next steps:
1. Log out and log back in to refresh group membership
2. Optional storage setup: ./automation/host-setup/setup-storage.sh
3. Optional host verification: ./automation/host-setup/verify-setup.sh
4. Continue with: make phase2

EOF

log_status "Next steps saved to /tmp/next-steps.txt"
