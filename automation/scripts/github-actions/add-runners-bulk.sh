#!/bin/bash
# add-runners-bulk.sh - settings.tomlのarc_repositoriesからTailscale OIDC workflowを一括作成

set +x
set -euo pipefail
unset GH_DEBUG DEBUG

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# 色定義
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ログ関数
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_status() {
    echo -e "${BLUE}[STATUS]${NC} $1"
}

# settings.tomlからarc_repositoriesを読み取る
parse_arc_repositories() {
    local settings_file="$PROJECT_ROOT/automation/settings.toml"

    if [[ ! -f "$settings_file" ]]; then
        log_error "settings.tomlが見つかりません: $settings_file"
        exit 1
    fi

    # Python/tomlパッケージがあるか確認
    if command -v python3 &> /dev/null; then
        # Pythonでパース（tomlライブラリ不要の簡易パーサー）
        python3 <<EOF
import re
import sys

settings_file = "$settings_file"

try:
    with open(settings_file, "r") as f:
        content = f.read()

    # arc_repositoriesセクションを探す
    # マルチライン配列をサポート
    pattern = r'arc_repositories\s*=\s*\[(.*?)\](?:\s*$|\s*#|\s*\[)'
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        sys.exit(0)

    repositories_text = match.group(1)

    # 各リポジトリエントリを厳密に検証
    # ["repo", min, max, "description", "latest"] 形式のみ許可
    entry_pattern = r'\[[^\[\]]+\]'
    repo_pattern = r'^\[\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"([^"]*)"\s*,\s*"(latest)"\s*\]$'

    entries = re.findall(entry_pattern, repositories_text)
    if not entries:
        sys.exit(0)

    for raw_entry in entries:
        normalized = raw_entry.strip()
        parsed = re.match(repo_pattern, normalized)
        if not parsed:
            print(
                f"Error: arc_repositories の形式が不正です: {normalized}",
                file=sys.stderr,
            )
            print(
                '期待形式: ["repo", min, max, "description", "latest"]',
                file=sys.stderr,
            )
            sys.exit(1)

        repo_name, min_runners, max_runners, description, strategy = parsed.groups()
        print(f"{repo_name}|{min_runners}|{max_runners}|{description}|{strategy}")

except Exception as e:
    print(f"Error parsing settings.toml: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    else
        # Pythonが利用できない場合の厳密パース
        log_warning "Pythonが利用できません。Bashで厳密パースを使用します。"

        local line
        local repo_regex='^\["([^"]+)"[[:space:]]*,[[:space:]]*([0-9]+)[[:space:]]*,[[:space:]]*([0-9]+)[[:space:]]*,[[:space:]]*"([^"]*)"[[:space:]]*,[[:space:]]*"(latest)"[[:space:]]*\],?$'
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            if [[ $line =~ $repo_regex ]]; then
                echo "${BASH_REMATCH[1]}|${BASH_REMATCH[2]}|${BASH_REMATCH[3]}|${BASH_REMATCH[4]}|${BASH_REMATCH[5]}"
            else
                log_error "arc_repositories の形式が不正です: $line"
                log_error '期待形式: ["repo", min, max, "description", "latest"]'
                return 1
            fi
        done < <(awk '/^arc_repositories = \[/,/^\]/' "$settings_file" | grep -E '^\s*\[')
    fi
}

# メイン処理
main() {
    log_info "========================================="
    log_info "Tailscale OIDC workflow一括作成開始"
    log_info "========================================="

    # settings.tomlからリポジトリリストを取得
    log_status "settings.tomlからarc_repositoriesを読み取り中..."

    repositories=()
    parsed_output=""
    if ! parsed_output="$(parse_arc_repositories)"; then
        log_error "arc_repositoriesの読み取りに失敗しました。形式を確認してください。"
        exit 1
    fi

    while IFS='|' read -r repo_name min_runners max_runners description strategy; do
        [[ -z "$repo_name" ]] && continue
        repositories+=("$repo_name|$min_runners|$max_runners|$description|$strategy")
    done <<< "$parsed_output"

    if [[ ${#repositories[@]} -eq 0 ]]; then
        log_warning "arc_repositoriesが設定されていません。"
        log_info "settings.tomlの[github]セクションにarc_repositoriesを設定してください。"
        exit 0
    fi

    log_info "合計 ${#repositories[@]} 個のリポジトリが見つかりました。"
    echo ""

    # 各リポジトリに対してadd-runner.shを実行
    success_count=0
    failed_count=0
    failed_repos=()

    for repo_entry in "${repositories[@]}"; do
        IFS='|' read -r repo_name min_runners max_runners description strategy <<< "$repo_entry"

        if [[ "$strategy" != "latest" ]]; then
            log_error "✗ $repo_name のstrategyが不正です: $strategy"
            failed_repos+=("$repo_name")
            failed_count=$((failed_count + 1))
            echo ""
            continue
        fi

        log_info "-----------------------------------------"
        log_info "リポジトリ: $repo_name"
        log_info "説明: ${description:-設定なし}"
        log_info "GitHub-hosted runner を使用します（旧min/maxは無視）。strategy=$strategy"
        log_status "Tailscale OIDC workflow作成中..."

        # add-runner.shを実行（サブシェルで実行して親シェルへの影響を防ぐ）
        set +e
        (
            "$SCRIPT_DIR/add-runner.sh" "$repo_name" "$strategy" "$@"
        )
        result=$?
        set -e

        if [[ $result -eq 0 ]]; then
            log_info "✓ $repo_name のTailscale OIDC workflow作成成功"
            success_count=$((success_count + 1))
        else
            log_error "✗ $repo_name のTailscale OIDC workflow作成失敗 (exit code: $result)"
            failed_repos+=("$repo_name")
            failed_count=$((failed_count + 1))
        fi

        echo ""
    done

    # 結果サマリー
    log_info "========================================="
    log_info "Tailscale OIDC workflow一括作成完了"
    log_info "========================================="
    log_info "成功: $success_count / ${#repositories[@]}"

    if [[ $failed_count -gt 0 ]]; then
        log_warning "失敗: $failed_count"
        log_warning "失敗したリポジトリ:"
        for repo in "${failed_repos[@]}"; do
            echo "  - $repo"
        done
        exit 1
    else
        log_info "すべてのTailscale OIDC workflowが正常に作成されました。"
    fi
}

# スクリプト実行
main "$@"
