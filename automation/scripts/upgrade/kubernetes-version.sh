# shellcheck shell=bash
# Kubernetes の目標版は repo 直下の kubernetes-version.txt だけで決める（Renovate が PR で更新する）。
# settings-loader.sh は SCRIPT_DIR を上書きするため、ここでは BASH_SOURCE からパスを求める。

KUBERNETES_VERSION_FILE="${KUBERNETES_VERSION_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/kubernetes-version.txt}"

# kubernetes-version.txt の版（例: v1.36.0）を出力する。
read_kubernetes_version() {
  local version
  if [[ ! -f "$KUBERNETES_VERSION_FILE" ]]; then
    log_error "kubernetes-version.txt が見つかりません: $KUBERNETES_VERSION_FILE"
    return 1
  fi
  version="$(<"$KUBERNETES_VERSION_FILE")"
  version="${version//[[:space:]]/}"
  if [[ ! "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    log_error "kubernetes-version.txt の形式が不正です（例: v1.36.0）: '${version}'"
    return 1
  fi
  printf '%s\n' "$version"
}

# upgrade の目標版。settings.toml の旧設定 upgrade.target_version が食い違うときは止める。
resolve_target_version() {
  local file_version legacy=""
  file_version="$(read_kubernetes_version)" || return 1
  if declare -f get_config >/dev/null 2>&1; then
    legacy="$(get_config upgrade target_version "")"
  fi
  if [[ -n "$legacy" && "$legacy" != "$file_version" ]]; then
    log_error "settings.toml の upgrade.target_version (${legacy}) が kubernetes-version.txt (${file_version}) と違います。settings.toml から target_version を消してください"
    return 1
  fi
  printf '%s\n' "$file_version"
}

# v1.36.2 -> 1.36
kubernetes_minor() {
  local version="${1#v}"
  printf '%s\n' "${version%.*}"
}
