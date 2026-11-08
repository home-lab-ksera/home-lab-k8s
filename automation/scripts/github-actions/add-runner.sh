#!/usr/bin/env bash
# GitHub-hosted runner + Tailscale OIDC 用 workflow と private repo Secrets の準備。
# bash -x / GH_DEBUG でも資格情報をログへ出さない。
set +x
set -euo pipefail
unset GH_DEBUG DEBUG

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../common-logging.sh"

usage() {
    cat <<'USAGE'
使用方法: add-runner.sh <repo|owner/repo> [latest] [--force] [--non-interactive]
GitHub から公開範囲・既定ブランチを取得し、workflow を生成します。
private repo は4つの Secrets を登録します（同名の既存値は更新）。
  TS_OAUTH_CLIENT_ID / TS_AUDIENCE / HARBOR_USERNAME / HARBOR_PASSWORD
値は環境変数、未指定なら端末で非表示入力します。引数には渡さないでください。
--non-interactive: 入力待ちをせず、必要な値が不足していたら失敗します。
--force: 生成先の既存 workflow を上書きします。
所有者: GITHUB_USERNAME（既定 home-lab-ksera）、出力先: WORKFLOW_DIR（既定 .github/workflows）
USAGE
}
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi
if [[ $# -eq 0 ]]; then usage >&2; exit 1; fi
REPOSITORY="$1"
shift
FORCE=false
NON_INTERACTIVE=false
POSITIONAL=()
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
        --non-interactive) NON_INTERACTIVE=true ;;
        --*) log_error "不明なオプション: $arg"; exit 1 ;;
        *) POSITIONAL+=("$arg") ;;
    esac
done
case ${#POSITIONAL[@]} in
    0) STRATEGY=latest ;;
    1) STRATEGY="${POSITIONAL[0]}" ;;
    3)
        [[ "${POSITIONAL[0]}" =~ ^[0-9]+$ && "${POSITIONAL[1]}" =~ ^[0-9]+$ ]] || exit 1
        STRATEGY="${POSITIONAL[2]}"
        log_warning "旧 min/max は使用しません。GitHub-hosted runner を使用します。"
        ;;
    *) usage >&2; exit 1 ;;
esac
[[ "$STRATEGY" == latest ]] || { log_error "strategy は latest のみ指定可能です"; exit 1; }
if [[ "$REPOSITORY" != */* ]]; then
    REPOSITORY="${GITHUB_USERNAME:-home-lab-ksera}/$REPOSITORY"
fi
[[ "$REPOSITORY" =~ ^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
    log_error "リポジトリ名が不正です"; exit 1;
}
command -v gh >/dev/null || { log_error "GitHub CLI (gh) が必要です。gh auth login で認証してください"; exit 1; }
command -v python3 >/dev/null || { log_error "python3 が必要です"; exit 1; }
# API 失敗時に public と見なしたり、既定ブランチを推測したりしない。
METADATA="$(gh api "repos/$REPOSITORY")"
CONFIG="$(printf '%s' "$METADATA" | python3 -c '
import json, re, sys
repo = json.load(sys.stdin)
name, branch, private = repo["name"], repo["default_branch"], repo["private"]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
    sys.exit("Unsupported repository name")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch):
    sys.exit("Unsupported default branch name")
if type(private) is not bool:
    sys.exit("Invalid repository visibility")
print(name, branch, str(private).lower())
')"
read -r REPOSITORY_NAME DEFAULT_BRANCH PRIVATE <<< "$CONFIG"
WORKFLOW_DIR="${WORKFLOW_DIR:-.github/workflows}"
WORKFLOW_FILE="$WORKFLOW_DIR/build-and-push-$REPOSITORY_NAME.yml"
if [[ -e "$WORKFLOW_FILE" && "$FORCE" != true ]]; then
    log_error "既存 workflow は変更しません: $WORKFLOW_FILE（上書きする場合は --force）"
    exit 1
fi
mkdir -p "$WORKFLOW_DIR"
TEMP_WORKFLOW="$(mktemp "$WORKFLOW_DIR/.harbor-oidc.XXXXXX")"
trap 'rm -f "$TEMP_WORKFLOW"' EXIT
python3 - "$SCRIPT_DIR/harbor-oidc.yml.tmpl" "$TEMP_WORKFLOW" "$REPOSITORY_NAME" "$DEFAULT_BRANCH" <<'PY'
from pathlib import Path
import sys
template, output, repo, branch = sys.argv[1:]
text = Path(template).read_text()
for key, value in {"REPOSITORY": repo, "IMAGE": repo.lower(), "BRANCH": branch}.items():
    text = text.replace("@@" + key + "@@", value)
Path(output).write_text(text)
PY

if [[ "$PRIVATE" == true ]]; then
    SECRET_NAMES=(TS_OAUTH_CLIENT_ID TS_AUDIENCE HARBOR_USERNAME HARBOR_PASSWORD)
    # 4つすべてを揃えてから書き込みを始める。入力値はファイルや引数に残さない。
    for name in "${SECRET_NAMES[@]}"; do
        if [[ -z "${!name:-}" ]]; then
            if [[ "$NON_INTERACTIVE" == true || ! -t 0 ]]; then
                log_error "$REPOSITORY: $name が未指定です。環境変数で渡すか端末から実行してください。"
                exit 1
            fi
            read -r -s -p "$REPOSITORY / $name: " "${name?}"
            printf '\n' >&2
            [[ -n "${!name}" ]] || { log_error "$name は空にできません"; exit 1; }
        fi
    done
    for name in "${SECRET_NAMES[@]}"; do
        if ! printf '%s' "${!name}" | gh secret set "$name" --repo "$REPOSITORY"; then
            log_error "$name の登録に失敗しました。先に登録済みの値は残ります。修正後に再実行してください。"
            exit 1
        fi
        log_status "$REPOSITORY: $name を登録しました"
    done
else
    log_status "public repo: Organization Secrets の4項目を利用します。対象 repo への公開設定を確認してください。"
fi

mv -f "$TEMP_WORKFLOW" "$WORKFLOW_FILE"
log_status "workflow を作成しました: $WORKFLOW_FILE"
log_status "対象: $REPOSITORY / 既定ブランチ: $DEFAULT_BRANCH / private: $PRIVATE"
log_status "Tailscale の信頼条件で refs/heads/$DEFAULT_BRANCH の push を許可してください。"
log_status "workflow を対象 repo へ反映してください。runner group / ScaleSet の追加は不要です。"
