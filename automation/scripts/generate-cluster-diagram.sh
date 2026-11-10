#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_FILE="$ROOT_DIR/cluster-diagram.png"
NAMESPACE_LIST=(
  argocd
  harbor
  cert-manager
  external-secrets
  nginx-gateway
  metallb-system
  apps
)

# 共通関数読み込み
source "$SCRIPT_DIR/common-logging.sh"
source "$SCRIPT_DIR/common-ssh.sh"

check_dependencies() {
  if ! command -v ssh >/dev/null 2>&1; then
    log_error "ssh コマンドが見つかりません"
    exit 1
  fi

  if command -v kube-diagrams >/dev/null 2>&1; then
    DIAGRAM_MODE="local"
    log_status "kube-diagrams (ローカル) を使用します"
    return 0
  fi

  if command -v docker >/dev/null 2>&1; then
    DIAGRAM_MODE="docker"
    log_status "kube-diagrams (Docker) を使用します"
    return 0
  fi

  log_error "kube-diagrams も docker も見つかりません"
  log_error "例: pip install KubeDiagrams または docker install を実施してください"
  exit 1
}

append_resource_yaml() {
  local resource="$1"
  local temp_yaml="$2"
  local fetched

  if fetched="$(k8s_ssh_control "kubectl get $resource --all-namespaces -o yaml" 2>/dev/null)"; then
    printf "%s\n" "$fetched" >> "$temp_yaml"
    printf "%s\n" "---" >> "$temp_yaml"
    log_info "取得成功: $resource"
  else
    log_warning "取得スキップ: $resource (未導入または取得不可)"
  fi
}

generate_namespace_diagram() {
  local input_yaml="$1"
  local namespace="$2"
  local output_png="$3"

  case "$DIAGRAM_MODE" in
    local)
      kube-diagrams --without-namespace -n "$namespace" - -o "$output_png" < "$input_yaml"
      ;;
    docker)
      docker run --rm -i --user "$(id -u):$(id -g)" -v "$ROOT_DIR:/work" philippemerle/kubediagrams \
        kube-diagrams --without-namespace -n "$namespace" - -o "/work/${output_png##$ROOT_DIR/}" < "$input_yaml"
      ;;
    *)
      log_error "不正な DIAGRAM_MODE: $DIAGRAM_MODE"
      exit 1
      ;;
  esac
}

compose_diagrams() {
  local output_file="$1"
  shift
  python3 - "$output_file" "$@" <<'PY'
import math
import sys
from PIL import Image, ImageOps, ImageDraw

out = sys.argv[1]
files = sys.argv[2:]
images = []
for f in files:
    img = Image.open(f).convert("RGB")
    # 横長を抑えるため高さ基準でリサイズ
    h = 900
    w = max(800, int(img.width * (h / img.height)))
    img = img.resize((w, h), Image.Resampling.LANCZOS)
    images.append((f, img))

if not images:
    raise SystemExit("no images")

cols = 2
rows = math.ceil(len(images) / cols)
cell_w = max(img.width for _, img in images) + 40
cell_h = max(img.height for _, img in images) + 70
canvas = Image.new("RGB", (cell_w * cols, cell_h * rows), "white")
draw = ImageDraw.Draw(canvas)

for idx, (name, img) in enumerate(images):
    r = idx // cols
    c = idx % cols
    x = c * cell_w + (cell_w - img.width) // 2
    y = r * cell_h + 40
    canvas.paste(img, (x, y))
    title = name.split("/")[-1].replace(".png", "")
    draw.text((c * cell_w + 20, r * cell_h + 12), title, fill="black")

canvas.save(out, format="PNG")
print(f"saved {out}")
PY
}

main() {
  local temp_yaml
  local temp_dir
  local namespace
  local generated_files=()
  temp_yaml="$(mktemp)"
  temp_dir="$(mktemp -d "$ROOT_DIR/.tmp-diagrams.XXXXXX")"
  trap "rm -f '$temp_yaml'; rm -rf '$temp_dir'" EXIT

  check_dependencies

  log_status "クラスタ構成情報の収集中..."

  # Pod/ReplicaSet を大量に含めると図が巨大化するため、主要リソースを選択して収集する
  append_resource_yaml "namespaces,nodes,deployments,statefulsets,daemonsets,services" "$temp_yaml"
  append_resource_yaml "ingress,networkpolicy,pvc,pv,storageclasses" "$temp_yaml"
  append_resource_yaml "applications.argoproj.io" "$temp_yaml"
  append_resource_yaml "certificates.cert-manager.io,issuers.cert-manager.io,clusterissuers.cert-manager.io" "$temp_yaml"
  append_resource_yaml "externalsecrets.external-secrets.io,secretstores.external-secrets.io,clustersecretstores.external-secrets.io" "$temp_yaml"
  append_resource_yaml "gateways.gateway.networking.k8s.io,httproutes.gateway.networking.k8s.io" "$temp_yaml"

  if [[ ! -s "$temp_yaml" ]]; then
    log_error "YAML データ取得に失敗しました。SSH 接続や kubectl 権限を確認してください"
    exit 1
  fi

  log_status "Namespace ごとの構成図を生成中..."
  for namespace in "${NAMESPACE_LIST[@]}"; do
    local ns_output
    ns_output="$temp_dir/${namespace}.png"
    if generate_namespace_diagram "$temp_yaml" "$namespace" "$ns_output" >/dev/null 2>&1; then
      generated_files+=("$ns_output")
      log_info "生成成功: namespace=$namespace"
    else
      log_warning "生成スキップ: namespace=$namespace (リソースなし/未導入)"
    fi
  done

  if [[ ${#generated_files[@]} -eq 0 ]]; then
    log_error "有効な namespace 構成図を生成できませんでした"
    exit 1
  fi

  log_status "構成図を合成中..."
  compose_diagrams "$OUTPUT_FILE" "${generated_files[@]}"

  if [[ -f "$OUTPUT_FILE" ]]; then
    local size
    size="$(wc -c < "$OUTPUT_FILE")"
    log_success "構成図を生成しました: $OUTPUT_FILE (${size} bytes)"
  else
    log_error "構成図の生成に失敗しました: $OUTPUT_FILE"
    exit 1
  fi
}

main "$@"
