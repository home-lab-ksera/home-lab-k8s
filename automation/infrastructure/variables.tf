# Phase 2: 改善された変数定義

variable "vm_user" {
  description = "VM内で使用するユーザー名"
  type        = string
  default     = "k8suser"
}

variable "ssh_public_key" {
  description = "VM接続用のSSH公開鍵"
  type        = string
  default     = ""
}

variable "control_plane_ip" {
  description = "Control PlaneのIPアドレス"
  type        = string
  default     = "192.168.122.10"
}

# key はノード名。2VM 構成では k8s-worker2 だけを置く（tasks/capacity-optimization-plan.md 7）。
# 名前を詰めて k8s-worker1 にしないのは、local-path の PV が k8s-worker2 にノード固定されているため。
variable "workers" {
  description = "Worker Node の定義（key: ノード名）"
  type = map(object({
    ip     = string
    memory = number # MiB
    vcpu   = number
  }))
  default = {
    "k8s-worker2" = { ip = "192.168.122.12", memory = 8192, vcpu = 4 }
  }
}

variable "network_gateway" {
  description = "ネットワークゲートウェイ"
  type        = string
  default     = "192.168.122.1"
}

# VM仕様設定
# ホスト（N100 4コア / 15GiB）に対し VM 合計が物理メモリを超えないようにする。
# control-plane は workload も載せるため 5GiB。詳細は docs/host-capacity.md。
variable "control_plane_memory" {
  description = "Control PlaneのメモリサイズMB"
  type        = number
  default     = 5120 # 5GB
}

variable "control_plane_vcpu" {
  description = "Control PlaneのvCPU数"
  type        = number
  default     = 2
}

# Kubernetesクラスター設定
variable "kubernetes_version" {
  description = "Kubernetesバージョン"
  type        = string
  default     = "v1.33.0"
}

variable "pod_network_cidr" {
  description = "Pod ネットワークCIDR（Flannel用）"
  type        = string
  default     = "10.244.0.0/16"
}

variable "service_network_cidr" {
  description = "Service ネットワークCIDR"
  type        = string
  default     = "10.96.0.0/12"
}

variable "ssh_private_key_path" {
  description = "SSH秘密鍵のパス"
  type        = string
  default     = "~/.ssh/id_rsa"
}

variable "enable_nfs_mount" {
  description = "ホスト上のNFS共有をマウントする（使い捨てCIでは無効）"
  type        = bool
  default     = true
}
