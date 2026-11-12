# Phase 2: 出力値定義

output "cluster_id" {
  description = "クラスターの一意ID"
  value       = random_id.cluster.hex
}

output "control_plane_ip" {
  description = "Control PlaneのIPアドレス"
  value       = var.control_plane_ip
}

output "worker_ips" {
  description = "Worker NodeのIPアドレス一覧"
  value       = { for name, worker in var.workers : name => worker.ip }
}

output "ssh_connection_commands" {
  description = "VM接続用SSHコマンド"
  value = merge(
    { control_plane = "ssh ${var.vm_user}@${var.control_plane_ip}" },
    { for name, worker in var.workers : name => "ssh ${var.vm_user}@${worker.ip}" }
  )
}

output "vm_names" {
  description = "作成されたVM名"
  value = {
    control_plane = libvirt_domain.control_plane.name
    workers       = [for domain in libvirt_domain.worker : domain.name]
  }
}

output "kubeconfig_path" {
  description = "kubeconfigファイルのパス"
  value       = "/home/${var.vm_user}/.kube/config"
}

output "cluster_endpoint" {
  description = "Kubernetesクラスターエンドポイント"
  value       = "${var.control_plane_ip}:6443"
}

output "deployment_summary" {
  description = "デプロイメント完了情報"
  value = {
    cluster_name       = "k8s-cluster-${random_id.cluster.hex}"
    kubernetes_version = var.kubernetes_version
    nodes_total        = 1 + length(var.workers)
    control_plane_ip   = var.control_plane_ip
    worker_ips         = [for worker in var.workers : worker.ip]
    pod_network_cidr   = var.pod_network_cidr
    kubeconfig_cmd     = "scp ${var.vm_user}@${var.control_plane_ip}:/home/${var.vm_user}/.kube/config ~/.kube/config-k8s-cluster"
  }
}