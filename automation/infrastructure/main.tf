# Phase 2&3統合: VM構築+Kubernetesクラスター構築用Terraformコード
# ランダムIDを使用してリソース重複を回避

terraform {
  required_version = ">= 1.0"
  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "~> 0.7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.1"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.1"
    }
  }
}

locals {
  kubernetes_minor   = regex("^v[0-9]+\\.[0-9]+", var.kubernetes_version)
  kubernetes_package = "${trimprefix(var.kubernetes_version, "v")}-1.1"
  # cloud-init を生成するノード。key はホスト名。
  nodes = merge(
    { "k8s-control-plane" = var.control_plane_ip },
    { for name, worker in var.workers : name => worker.ip }
  )
}

# ランダムIDでユニークなリソース名を生成
resource "random_id" "cluster" {
  byte_length = 4
}

# libvirt provider設定
provider "libvirt" {
  uri = "qemu:///system"
}

# Ubuntu 24.04 LTS Server イメージ
resource "libvirt_volume" "ubuntu_base" {
  name   = "ubuntu-base-${random_id.cluster.hex}.img"
  source = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
  pool   = "default"
  format = "qcow2"
}

# Control Plane VM用ディスク
resource "libvirt_volume" "control_plane_disk" {
  name           = "k8s-control-plane-${random_id.cluster.hex}.qcow2"
  base_volume_id = libvirt_volume.ubuntu_base.id
  pool           = "default"
  size           = 53687091200 # 50GB
}

# Worker Node ディスク
resource "libvirt_volume" "worker_disk" {
  for_each       = var.workers
  name           = "${each.key}-${random_id.cluster.hex}.qcow2"
  base_volume_id = libvirt_volume.ubuntu_base.id
  pool           = "default"
  # 動画spool（最大5GiB + 空き4GiB）とpublish runnerの一時領域を確保する。
  size = 64424509440 # 60GiB。既存VMの拡張は docs/worker-disk.md を参照。
}

# 簡素化されたcloud-init設定
data "template_file" "user_data" {
  for_each = local.nodes
  template = file("${path.module}/cloud-init/user-data.yaml")
  vars = {
    vm_hostname      = each.key
    username         = var.vm_user
    ssh_key          = var.ssh_public_key
    enable_nfs_mount = var.enable_nfs_mount
  }
}

data "template_file" "network_config" {
  for_each = local.nodes
  template = file("${path.module}/cloud-init/network-config.yaml")
  vars = {
    ip_address = each.value
    gateway    = var.network_gateway
  }
}

# Control Plane cloud-init
resource "libvirt_cloudinit_disk" "control_plane_init" {
  name           = "k8s-control-plane-init-${random_id.cluster.hex}.iso"
  pool           = "default"
  user_data      = data.template_file.user_data["k8s-control-plane"].rendered
  network_config = data.template_file.network_config["k8s-control-plane"].rendered
}

# Worker Node cloud-init
resource "libvirt_cloudinit_disk" "worker_init" {
  for_each       = var.workers
  name           = "${each.key}-init-${random_id.cluster.hex}.iso"
  pool           = "default"
  user_data      = data.template_file.user_data[each.key].rendered
  network_config = data.template_file.network_config[each.key].rendered
}

# Control Plane VM
resource "libvirt_domain" "control_plane" {
  name   = "k8s-control-plane-${random_id.cluster.hex}"
  memory = var.control_plane_memory
  vcpu   = var.control_plane_vcpu

  # x86-64-v2 を満たす CPU を公開する。host-passthrough は現行ホストで不安定なため使わない。
  cpu {
    mode = "host-model"
  }

  # libvirt は起動時に host-model を具体的な CPU モデルへ展開し、mode を custom として返す。
  # 毎回の plan で差分にならないよう比較から外す。
  lifecycle {
    ignore_changes = [cpu]
  }

  # provider が持たない libvirt 設定（memballoon の free page reporting）を補う。
  xml {
    xslt = file("${path.module}/libvirt-domain.xsl")
  }

  cloudinit = libvirt_cloudinit_disk.control_plane_init.id

  network_interface {
    network_name   = "default"
    wait_for_lease = false
    # 静的IPはcloud-initで設定する。DHCP lease由来のaddressesには重複指定しない。
  }

  disk {
    volume_id = libvirt_volume.control_plane_disk.id
  }

  console {
    type        = "pty"
    target_port = "0"
    target_type = "serial"
  }

  graphics {
    type        = "spice"
    listen_type = "address"
    autoport    = true
  }
}

# Worker Node VMs
resource "libvirt_domain" "worker" {
  for_each = var.workers
  name     = "${each.key}-${random_id.cluster.hex}"
  memory   = each.value.memory
  vcpu     = each.value.vcpu

  cpu {
    mode = "host-model"
  }

  # libvirt は起動時に host-model を具体的な CPU モデルへ展開し、mode を custom として返す。
  # 毎回の plan で差分にならないよう比較から外す。
  lifecycle {
    ignore_changes = [cpu]
  }

  # provider が持たない libvirt 設定（memballoon の free page reporting）を補う。
  xml {
    xslt = file("${path.module}/libvirt-domain.xsl")
  }

  cloudinit = libvirt_cloudinit_disk.worker_init[each.key].id

  network_interface {
    network_name   = "default"
    wait_for_lease = false
    # 静的IPはcloud-initで設定する。DHCP lease由来のaddressesには重複指定しない。
  }

  disk {
    volume_id = libvirt_volume.worker_disk[each.key].id
  }

  console {
    type        = "pty"
    target_port = "0"
    target_type = "serial"
  }

  graphics {
    type        = "spice"
    listen_type = "address"
    autoport    = true
  }
}

# VMの起動完了を待つためのリソース
resource "null_resource" "wait_for_vms" {
  depends_on = [
    libvirt_domain.control_plane,
    libvirt_domain.worker
  ]

  provisioner "local-exec" {
    command = "sleep 60" # VMの起動とcloud-init完了を待つ
  }
}

# Kubernetes環境セットアップ（Control Plane）
resource "null_resource" "k8s_control_plane_setup" {
  depends_on = [null_resource.wait_for_vms]

  connection {
    type        = "ssh"
    host        = var.control_plane_ip
    user        = var.vm_user
    private_key = file(var.ssh_private_key_path)
  }

  # cloud-init完了を待機
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo timeout 600 cloud-init status --wait"
    ]
  }

  # Kubernetesパッケージインストール
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo apt update",
      "sudo apt install -y apt-transport-https ca-certificates curl gpg containerd python3-yaml",
      "curl -fsSL https://pkgs.k8s.io/core:/stable:/${local.kubernetes_minor}/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg",
      "echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/${local.kubernetes_minor}/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list",
      "sudo apt update",
      "sudo apt install -y kubelet=${local.kubernetes_package} kubeadm=${local.kubernetes_package} kubectl=${local.kubernetes_package}",
      "sudo apt-mark hold kubelet kubeadm kubectl"
    ]
  }

  # カーネルモジュールとネットワーク設定
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo modprobe br_netfilter",
      "sudo modprobe overlay",
      "echo 'br_netfilter' | sudo tee /etc/modules-load.d/k8s.conf",
      "echo 'overlay' | sudo tee -a /etc/modules-load.d/k8s.conf",
      "echo 'net.bridge.bridge-nf-call-iptables = 1' | sudo tee /etc/sysctl.d/k8s.conf",
      "echo 'net.bridge.bridge-nf-call-ip6tables = 1' | sudo tee -a /etc/sysctl.d/k8s.conf",
      "echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/k8s.conf",
      "sudo sysctl --system"
    ]
  }

  # containerd設定
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo mkdir -p /etc/containerd",
      "sudo containerd config default | sudo tee /etc/containerd/config.toml",
      "sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml",
      "sudo systemctl restart containerd",
      "sudo systemctl enable containerd"
    ]
  }

  # 暗号鍵は VM 内で生成し、Terraform state / provisioner 出力へ渡さない。
  provisioner "file" {
    source      = "${path.module}/apiserver-security"
    destination = "/tmp/apiserver-security"
  }

  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo python3 /tmp/apiserver-security/configure.py prepare"
    ]
  }

  # control-plane は workload も載せるため、静的 Pod 用の予約と eviction 閾値を drop-in で足す。
  # /var/lib/kubelet/config.yaml は kubeadm upgrade で再生成されるため使わない。
  # drop-in の evictionHard は既定値を丸ごと置き換えるため、既定の項目も並べる。
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo mkdir -p /etc/kubernetes/kubelet.conf.d",
      "echo 'KUBELET_EXTRA_ARGS=--config-dir=/etc/kubernetes/kubelet.conf.d' | sudo tee /etc/default/kubelet",
      <<-EOF
        sudo tee /etc/kubernetes/kubelet.conf.d/50-control-plane-reserved.conf > /dev/null <<EOL
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
systemReserved:
  cpu: 200m
  memory: 512Mi
evictionHard:
  memory.available: 300Mi
  nodefs.available: 10%
  nodefs.inodesFree: 5%
  imagefs.available: 15%
  imagefs.inodesFree: 5%
EOL
      EOF
    ]
  }

  # kubeadm設定ファイル作成
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      <<-EOF
        sudo tee /tmp/kubeadm-config.yaml > /dev/null <<EOL
apiVersion: kubeadm.k8s.io/v1beta4
kind: InitConfiguration
localAPIEndpoint:
  advertiseAddress: ${var.control_plane_ip}
  bindPort: 6443
nodeRegistration:
  # 2VM 構成では control-plane にも workload を置く。
  taints: []
---
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
kubernetesVersion: ${var.kubernetes_version}
controlPlaneEndpoint: "${var.control_plane_ip}:6443"
networking:
  podSubnet: "${var.pod_network_cidr}"
  serviceSubnet: "${var.service_network_cidr}"
---
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
cgroupDriver: systemd
# serving CSR は bootstrap の台帳照合付き承認処理が初回・更新とも処理する。
serverTLSBootstrap: true
EOL
      EOF
    ]
  }

  # kubeadmクラスター初期化
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo python3 /tmp/apiserver-security/configure.py kubeadm --input /tmp/kubeadm-config.yaml --output /tmp/kubeadm-config-secure.yaml",
      "sudo kubeadm init --config=/tmp/kubeadm-config-secure.yaml --upload-certs",
      "mkdir -p /home/${var.vm_user}/.kube",
      "sudo cp -i /etc/kubernetes/admin.conf /home/${var.vm_user}/.kube/config",
      "sudo chown ${var.vm_user}:${var.vm_user} /home/${var.vm_user}/.kube/config",
      # kubeadm が付けるこの label があると、MetalLB は control-plane から LoadBalancer IP を広告しない。
      "kubectl label node k8s-control-plane node.kubernetes.io/exclude-from-external-load-balancers-",
      "kubeadm token create --print-join-command > /tmp/worker-join-command.txt"
    ]
  }

  # Harbor用ストレージディレクトリ作成
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo mkdir -p /tmp/harbor-registry /tmp/harbor-database /tmp/harbor-redis /tmp/harbor-trivy /tmp/harbor-jobservice",
      "sudo chmod 777 /tmp/harbor-*"
    ]
  }
}

# Worker Nodeセットアップ
resource "null_resource" "k8s_worker_setup" {
  for_each   = var.workers
  depends_on = [null_resource.k8s_control_plane_setup]

  connection {
    type        = "ssh"
    host        = each.value.ip
    user        = var.vm_user
    private_key = file(var.ssh_private_key_path)
  }

  # cloud-init完了を待機
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo timeout 600 cloud-init status --wait"
    ]
  }

  # Kubernetesパッケージインストール
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo apt update",
      "sudo apt install -y apt-transport-https ca-certificates curl gpg containerd",
      "curl -fsSL https://pkgs.k8s.io/core:/stable:/${local.kubernetes_minor}/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg",
      "echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/${local.kubernetes_minor}/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list",
      "sudo apt update",
      "sudo apt install -y kubelet=${local.kubernetes_package} kubeadm=${local.kubernetes_package} kubectl=${local.kubernetes_package}",
      "sudo apt-mark hold kubelet kubeadm kubectl"
    ]
  }

  # カーネルモジュールとネットワーク設定
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo modprobe br_netfilter",
      "sudo modprobe overlay",
      "echo 'br_netfilter' | sudo tee /etc/modules-load.d/k8s.conf",
      "echo 'overlay' | sudo tee -a /etc/modules-load.d/k8s.conf",
      "echo 'net.bridge.bridge-nf-call-iptables = 1' | sudo tee /etc/sysctl.d/k8s.conf",
      "echo 'net.bridge.bridge-nf-call-ip6tables = 1' | sudo tee -a /etc/sysctl.d/k8s.conf",
      "echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/k8s.conf",
      "sudo sysctl --system"
    ]
  }

  # containerd設定
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo mkdir -p /etc/containerd",
      "sudo containerd config default | sudo tee /etc/containerd/config.toml",
      "sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml",
      "sudo systemctl restart containerd",
      "sudo systemctl enable containerd"
    ]
  }

  # Harbor用ストレージディレクトリ作成
  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "sudo mkdir -p /tmp/harbor-registry /tmp/harbor-database /tmp/harbor-redis /tmp/harbor-trivy /tmp/harbor-jobservice",
      "sudo chmod 777 /tmp/harbor-*"
    ]
  }
}

# joinコマンドを取得してWorker Nodeに配布
resource "null_resource" "worker_join" {
  for_each   = var.workers
  depends_on = [null_resource.k8s_worker_setup]

  # joinコマンドをControl Planeから取得してローカルに保存
  provisioner "local-exec" {
    command = "ssh -o StrictHostKeyChecking=no -i ${var.ssh_private_key_path} ${var.vm_user}@${var.control_plane_ip} 'cat /tmp/worker-join-command.txt' > /tmp/worker-join-${random_id.cluster.hex}-${each.key}.sh && chmod 600 /tmp/worker-join-${random_id.cluster.hex}-${each.key}.sh"
  }

  # joinコマンドをWorker Nodeにコピー
  provisioner "file" {
    connection {
      type        = "ssh"
      host        = each.value.ip
      user        = var.vm_user
      private_key = file(var.ssh_private_key_path)
    }

    source      = "/tmp/worker-join-${random_id.cluster.hex}-${each.key}.sh"
    destination = "/tmp/worker-join-command.sh"
  }

  # Worker Nodeでjoin実行
  provisioner "remote-exec" {
    connection {
      type        = "ssh"
      host        = each.value.ip
      user        = var.vm_user
      private_key = file(var.ssh_private_key_path)
    }

    inline = [
      "set -eu",
      "chmod +x /tmp/worker-join-command.sh",
      "sudo bash /tmp/worker-join-command.sh"
    ]
  }
}

# Helm 3 インストール
resource "null_resource" "helm_install" {
  depends_on = [null_resource.worker_join]

  connection {
    type        = "ssh"
    host        = var.control_plane_ip
    user        = var.vm_user
    private_key = file(var.ssh_private_key_path)
  }

  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "# Helm 3 インストール",
      "curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash",
      "helm version",
      "echo 'Helm installation completed successfully'"
    ]
  }
}

# FlannelCNI インストール
resource "null_resource" "flannel_install" {
  depends_on = [null_resource.helm_install]

  connection {
    type        = "ssh"
    host        = var.control_plane_ip
    user        = var.vm_user
    private_key = file(var.ssh_private_key_path)
  }

  provisioner "remote-exec" {
    inline = [
      "set -eu",
      "timeout 300 bash -c 'until kubectl get nodes; do sleep 10; done'",
      "kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml",
      "sleep 30",
      "echo '=== Cluster Info ===' > /tmp/k8s-cluster-info.txt",
      "kubectl get nodes -o wide >> /tmp/k8s-cluster-info.txt",
      "echo '' >> /tmp/k8s-cluster-info.txt",
      "kubectl get pods --all-namespaces >> /tmp/k8s-cluster-info.txt"
    ]
  }
}
