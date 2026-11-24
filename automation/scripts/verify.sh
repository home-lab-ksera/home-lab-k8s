#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=automation/scripts/common-logging.sh
source "$SCRIPT_DIR/common-logging.sh"

if [ -f "$SCRIPT_DIR/settings-loader.sh" ]; then
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/settings-loader.sh" load 2>/dev/null || true
fi

CONTROL_PLANE_IP="${K8S_CONTROL_PLANE_IP:-192.168.122.10}"
K8S_USER="${K8S_USER:-k8suser}"
K8S_SSH_KEY="${K8S_SSH_KEY:-}"
REMOTE_KUBECONFIG="/etc/kubernetes/admin.conf"
KUBECONFIG_TARGET="${KUBECONFIG:-$HOME/.kube/config}"
KUBECONFIG_TARGET="${KUBECONFIG_TARGET%%:*}"

# shellcheck source=automation/scripts/common-ssh.sh
source "$SCRIPT_DIR/common-ssh.sh"
ssh_opts=()
k8s_ssh_opts ssh_opts "$K8S_SSH_KEY" -T -o ConnectTimeout=10

ssh_cmd=(ssh "${ssh_opts[@]}" "${K8S_USER}@${CONTROL_PLANE_IP}")

log_status "=== Phase 5: Verify ==="

verify_failures=0

if [[ "${CI:-}" == "true" || "${CI:-}" == "1" || "${VERIFY_SKIP_SSH:-}" == "true" ]]; then
  log_warning "CIモードのためSSH検証をスキップします（VERIFY_SKIP_SSH=true でもスキップ）"
  exit 0
fi

log_status "kubeconfig 同期中..."
mkdir -p "$(dirname "$KUBECONFIG_TARGET")"
if [[ -f "$KUBECONFIG_TARGET" ]]; then
  backup_path="${KUBECONFIG_TARGET}.bak-$(date +%Y%m%d_%H%M%S)"
  cp "$KUBECONFIG_TARGET" "$backup_path" || true
  log_status "kubeconfig をバックアップしました: ${backup_path}"
fi
if "${ssh_cmd[@]}" "test -f ${REMOTE_KUBECONFIG}"; then
  if "${ssh_cmd[@]}" "sudo cat ${REMOTE_KUBECONFIG}" > "$KUBECONFIG_TARGET"; then
    chmod 600 "$KUBECONFIG_TARGET" || true
    log_status "kubeconfig を更新しました: ${KUBECONFIG_TARGET}"
  else
    log_warning "kubeconfig の取得に失敗しました（sudo 権限を確認してください）"
  fi
else
  log_warning "${REMOTE_KUBECONFIG} が見つかりません"
fi

log_status "Control Plane 接続確認中..."
if ! "${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl version >/dev/null 2>&1"; then
  log_error "kubectl に接続できません（kubeconfig または接続先を確認してください）"
  exit 1
fi

log_status "ArgoCDリフレッシュ中..."
"${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get application bootstrap-root -n argocd >/dev/null 2>&1 && sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl annotate application bootstrap-root -n argocd argocd.argoproj.io/refresh=hard --overwrite" || true
"${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get application platform -n argocd >/dev/null 2>&1 && sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl annotate application platform -n argocd argocd.argoproj.io/refresh=hard --overwrite" || true
sleep 5

log_status "主要Namespace確認中..."
critical_namespaces=(argocd harbor external-secrets-system nginx-gateway metallb-system)
for namespace in "${critical_namespaces[@]}"; do
  if "${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get namespace ${namespace} >/dev/null 2>&1"; then
    log_status "✓ ${namespace} namespace 存在"
  else
    log_warning "${namespace} namespace が見つかりません"
  fi
done

log_status "ノード状態確認中..."
node_list=$("${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get nodes --no-headers" 2>/dev/null || true)
if [[ -z "$node_list" ]]; then
  log_error "ノード一覧を取得できませんでした"
  ((verify_failures++))
else
  total_nodes=$(echo "$node_list" | awk 'NF{count++} END {print count+0}')
  not_ready_nodes=$(echo "$node_list" | awk '$2 !~ /^Ready/ {count++} END {print count+0}')
  if [[ "$not_ready_nodes" -gt 0 ]]; then
    log_error "Ready でないノードがあります: ${not_ready_nodes}/${total_nodes}"
    echo "$node_list" | awk '$2 !~ /^Ready/ {printf "  - %s (%s)\n", $1, $2}'
    ((verify_failures++))
  else
    log_status "✓ 全ノード Ready: ${total_nodes}"
  fi
fi

log_status "Pod 状態確認中..."
pod_list=$("${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get pods -A --no-headers" 2>/dev/null || true)
if [[ -z "$pod_list" ]]; then
  log_error "Pod一覧を取得できませんでした"
  ((verify_failures++))
else
  problem_pod_count=$(echo "$pod_list" | awk '$4 != "Running" && $4 != "Completed" {count++} END {print count+0}')
  if [[ "$problem_pod_count" -gt 0 ]]; then
    log_error "Running/Completed 以外の Pod があります: ${problem_pod_count}"
    echo "$pod_list" | awk '$4 != "Running" && $4 != "Completed" {printf "  - %s/%s (%s)\n", $1, $2, $4; count++} count>=20 {exit}'
    ((verify_failures++))
  else
    log_status "✓ 異常Podなし"
  fi
fi

log_status "ArgoCD アプリ状態確認中..."
app_table=$("${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get applications -n argocd -o jsonpath='{range .items[*]}{.metadata.name}{\"\\t\"}{.status.sync.status}{\"\\t\"}{.status.health.status}{\"\\n\"}{end}'" 2>/dev/null || true)
if [[ -z "$app_table" ]]; then
  log_error "ArgoCDアプリ一覧を取得できませんでした"
  ((verify_failures++))
else
  app_total=$(echo "$app_table" | awk 'NF{count++} END {print count+0}')
  app_out_of_sync=$(echo "$app_table" | awk '$2 != "Synced" {count++} END {print count+0}')
  app_degraded=$(echo "$app_table" | awk '$3 != "Healthy" {count++} END {print count+0}')
  if [[ "$app_out_of_sync" -gt 0 || "$app_degraded" -gt 0 ]]; then
    log_error "ArgoCDアプリ異常: OutOfSync=${app_out_of_sync}, Degraded=${app_degraded}"
    echo "$app_table" | awk '$2 != "Synced" || $3 != "Healthy" {printf "  - %s (sync=%s, health=%s)\n", $1, $2, $3; count++} count>=20 {exit}'
    ((verify_failures++))
  else
    log_status "✓ ArgoCDアプリ正常: ${app_total}"
  fi
fi

log_status "ArgoCD OIDC 設定確認中..."
oidc_config=$("${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get configmap -n argocd argocd-cm -o jsonpath='{.data.oidc\\.config}'" 2>/dev/null || true)
if [[ -n "$oidc_config" ]]; then
  log_warning "argocd-cm に oidc.config が設定されています（Dexのみ運用なら削除推奨）"
else
  log_status "✓ OIDC設定なし（Dexのみ）"
fi

log_status "External Secrets 状態確認中..."
clusterstore_status=$("${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get clustersecretstore pulumi-esc-store -o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}'" 2>/dev/null || true)
if [[ "$clusterstore_status" == "True" ]]; then
  log_status "✓ ClusterSecretStore (pulumi-esc-store) Ready"
else
  log_error "ClusterSecretStore (pulumi-esc-store) が Ready ではありません"
  ((verify_failures++))
fi

externalsecret_table=$("${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get externalsecrets -A -o jsonpath='{range .items[*]}{.metadata.namespace}{\"/\"}{.metadata.name}{\"\\t\"}{.status.conditions[?(@.type==\"Ready\")].status}{\"\\n\"}{end}'" 2>/dev/null || true)
if [[ -n "$externalsecret_table" ]]; then
  externalsecret_not_ready=$(echo "$externalsecret_table" | awk '$2 != "True" {count++} END {print count+0}')
  if [[ "$externalsecret_not_ready" -gt 0 ]]; then
    log_error "ExternalSecret 未Ready: ${externalsecret_not_ready}"
    echo "$externalsecret_table" | awk '$2 != "True" {printf "  - %s (Ready=%s)\n", $1, $2; count++} count>=20 {exit}'
    ((verify_failures++))
  else
    log_status "✓ ExternalSecret 正常"
  fi
else
  log_error "ExternalSecret 一覧を取得できませんでした"
  ((verify_failures++))
fi

log_status "Gateway/LoadBalancer 確認中..."
gateway_count=$("${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get gateway -A --no-headers 2>/dev/null | wc -l" || echo "0")
if [[ "$gateway_count" -gt 0 ]]; then
  log_status "✓ Gateway リソース: ${gateway_count}"
else
  log_warning "Gateway リソースが見つかりません"
fi

gateway_lb_ip=$("${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get svc -n nginx-gateway nginx-gateway-nginx -o jsonpath='{.status.loadBalancer.ingress[0].ip}'" 2>/dev/null || true)
if [[ -n "$gateway_lb_ip" ]]; then
  log_status "✓ NGINX Gateway LoadBalancer IP: ${gateway_lb_ip}"
else
  log_warning "NGINX Gateway LoadBalancer IP が未割り当てです"
fi

log_status "ArgoCDアプリ一覧:"
"${ssh_cmd[@]}" "sudo KUBECONFIG=${REMOTE_KUBECONFIG} kubectl get applications -n argocd --no-headers" | awk '{print "  - " $1 " (" $2 "/" $3 ")"}' || true

if [[ "$verify_failures" -gt 0 ]]; then
  log_error "Phase 5 検証に失敗しました: ${verify_failures} 件"
  exit 1
fi

log_status "✓ Phase 5 検証に成功しました"
