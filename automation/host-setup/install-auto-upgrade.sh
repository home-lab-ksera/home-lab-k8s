#!/usr/bin/env bash
# k8s-auto-upgrade.timer をホストに入れる。自動更新専用の clone の中から、一般ユーザーで実行する。
#   git clone https://github.com/home-lab-ksera/home-lab-k8s.git ~/home-lab-k8s-auto
#   ~/home-lab-k8s-auto/automation/host-setup/install-auto-upgrade.sh
# systemd への登録だけ sudo を使う。何度実行してもよい。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SETTINGS_FILE="$REPO_DIR/automation/settings.toml"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
RUN_USER="$(id -un)"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "root ではなく、clone の所有者で実行してください（systemd の登録だけ sudo を使います）" >&2
  exit 1
fi

if [[ -n "$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)" ]]; then
  echo "$REPO_DIR に未コミットの変更があります。この clone は timer が毎回 main に戻すため、自動更新専用にしてください" >&2
  exit 1
fi

if [[ ! -f "$SETTINGS_FILE" ]]; then
  # 通知先は Alertmanager と同じ Slack。クラスタが止まっていても送れるよう、ここで写しておく
  webhook_url="$(kubectl -n observability get secret alertmanager-slack -o jsonpath='{.data.webhook-url}' 2>/dev/null | base64 -d 2>/dev/null || true)"
  if [[ -z "$webhook_url" ]]; then
    echo "警告: Slack の Webhook を取得できませんでした。通知は journald だけになります" >&2
  fi
  umask 077
  cat >"$SETTINGS_FILE" <<SETTINGS
# k8s-auto-upgrade 用（install-auto-upgrade.sh が生成）。Git 管理外。
[kubernetes]
user = "k8suser"
ssh_key_path = ""

[upgrade]
gate_retries = 3
gate_retry_wait_seconds = 30

[network]
control_plane_ip = "192.168.122.10"
worker_2_ip = "192.168.122.12"

[auto_upgrade]
slack_webhook_url = "${webhook_url}"
SETTINGS
  echo "作成しました: $SETTINGS_FILE"
fi

echo "--- 判定（dry-run）---"
"$REPO_DIR/automation/scripts/upgrade/auto-upgrade.sh" --dry-run

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
for unit in k8s-auto-upgrade.service k8s-auto-upgrade.timer; do
  sed -e "s|@USER@|${RUN_USER}|g" -e "s|@REPO@|${REPO_DIR}|g" "$SCRIPT_DIR/systemd/$unit" >"$tmp/$unit"
done

sudo /usr/bin/install -m 0644 "$tmp/k8s-auto-upgrade.service" "$tmp/k8s-auto-upgrade.timer" "$UNIT_DIR/"
sudo /usr/bin/systemctl daemon-reload
sudo /usr/bin/systemctl enable --now k8s-auto-upgrade.timer
/usr/bin/systemctl list-timers k8s-auto-upgrade.timer --no-pager
