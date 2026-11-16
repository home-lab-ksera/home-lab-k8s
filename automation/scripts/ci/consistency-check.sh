#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

check_failed=0

check_ok() {
  echo "[OK] $1"
}

check_ng() {
  echo "[NG] $1" >&2
  check_failed=1
}

python_ready=0
if ! command -v python3 >/dev/null 2>&1; then
  check_ng "python3 が見つかりません"
elif ! python3 -c 'import yaml' >/dev/null 2>&1; then
  check_ng "python3 の PyYAML が見つかりません"
else
  python_ready=1
fi

if [[ "$python_ready" -eq 1 ]]; then
  if python3 "$ROOT_DIR/automation/scripts/ci/consistency-check.py"; then
    check_ok "YAML 構造ベースの整合性を確認しました"
  else
    check_ng "YAML 構造ベースの整合性チェックに失敗しました"
  fi
fi

if [[ -f "$ROOT_DIR/docs/gitops.md" ]] \
  && grep -q 'docs/gitops.md' "$ROOT_DIR/README.md"; then
  check_ok "README の Application 一覧リンク先が存在します"
else
  check_ng "README の Application 一覧リンクが不正です"
fi

if [[ "$check_failed" -ne 0 ]]; then
  exit 1
fi
