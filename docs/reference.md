# 検証と参照

## 静的検証の入口

[mise.toml](../mise.toml) と [Python依存](../automation/toolchain/requirements.txt) に toolchain を定義し、
[validate.sh](../automation/scripts/ci/validate.sh) を CI とローカルで共有しています。

```bash
# Docker 内の専用 toolchain で実行
make validate
# ローカル（初回は docs/reference.md のセットアップを実施）
mise exec -- automation/scripts/ci/validate.sh
# 必要な CLI が導入済みの場合
make validate-local
```

validate は actionlint、setup / version update / observability / CI / PostgreSQL の回帰テスト、
shellcheck、yamllint、GitOps 参照・登録、policy、PostgreSQL 版整合、
全 `manifests/**/kustomization.yaml` の `kustomize build --enable-helm` と kubeconform、consistency / contract を確認します。
リモート配布物や schema の取得にネットワークを使います。

kubeconform は PGO 同梱 CRD・標準 schema・CRD catalog を利用し、schema 不足はスキップします。
外部 Helm Application の同期そのもの、admission、Secret の実値、URL 疎通、データ復元は保証しません。

## ローカルtoolchainのセットアップ

mise 2026.10.8 以降を用意し、Ubuntu/Debian では次を実行します。
Dockerでの `make validate` はこのホスト側セットアップを必要としません。

```bash
sudo apt-get update
sudo apt-get install -y bash ca-certificates curl git jq libpq-dev postgresql python3-venv unzip xz-utils
mise trust
mise install
mise run setup
mise run validate
```

`mise.toml` がCLIのバージョンを固定し、Python依存は `.venv/` に導入します。
`pg_config --bindir` をPATHへ追加するため、PostgreSQLの `initdb` / `pg_ctl` / `psql` も利用できます。
一時DBのテストがあるため一般ユーザーで実行してください。
CIも同じmise設定とrequirementsを使います。Docker版はDebianの専用イメージを作成し、
ホストと同じUID/GIDの一般ユーザーで検証するため、生成物がroot所有になりません。
kindやコンテナを使う追加実行テストは、Dockerが使えるホスト側で `mise exec --` を付けて実行します。

## CI の追加検証

[Deploy Process Validation](../.github/workflows/ci-deploy-validate.yml) は validate 以外も実行します。
`make validate` はこの workflow の全 job と同義ではありません。

| job | 内容・範囲 |
|---|---|
| `setup-behavior` | 外部コマンドを stub に置換した順序・失敗時停止・SSH skip の回帰テスト |
| `validate` | 上記 validate.sh |
| `platform-configs` | manifest の固定 image を使った Loki / Alloy 設定検証、Renovate 設定検証 |
| `postgresql-runtime` | 対象 path の変更時などに kind で更新・backup・restore。[詳細](postgresql-updates.md) |

追加設定検証のローカル入口（Docker が必要）:

```bash
mise exec -- python3 automation/scripts/ci/validate-platform-configs.py
```

## 使い捨て VM の実構築テスト

[Setup Integration](../.github/workflows/setup-integration.yml) は workflow_dispatch のみです。
[setup-integration.sh](../automation/scripts/ci/setup-integration.sh) が GitHub-hosted runner 内に隔離した設定を作り、
共通 Terraform / cloud-init から VM と Kubernetes を構築します。

```bash
gh workflow run setup-integration.yml --ref main
```

Node Ready、DNS・ノード間通信、Terraform 再適用の無差分、各 VM 再起動後の復旧を確認します。
成否にかかわらず診断収集と destroy を試み、artifact は 7 日保持します。
本番の NFS、phase1 のホスト準備、`clean-and-deploy.sh` の削除処理、ArgoCD 以降の収束は検証範囲外です。
実行可能性は runner の KVM・メモリ・ディスクの preflight に依存します。

## 個別検証

`mise exec -- bash` 内で必要な箇所だけ確認する例です。

```bash
git diff --check
shellcheck -S error -x automation/scripts/<file>.sh
yamllint -f parsable -c .yamllint.yml manifests/<dir-or-file>
kustomize build --enable-helm manifests/<component>
python3 automation/scripts/ci/gitops-check.py
python3 automation/scripts/ci/contract-check.py
python3 -m unittest discover -s automation/tests/setup -v
```

GitOps check はローカル source の HEAD、参照先、単一ファイルの directory.include、アプリ所有者と
bootstrap Kustomization の登録を確認します。policy check は重複所有、配置、許容する可変タグなどを検査します。
これらは Markdown 内のリンクを検証する仕組みではないため、文書移動時には相対リンクも確認します。

## 入口一覧

| 目的 | コマンド・正本 |
|---|---|
| ヘルプ | `make help`、[Makefile](../Makefile) |
| 構築 | `make phase1` → `phase2` → `bootstrap` → `phase5`。[削除範囲](bootstrap.md)を先に確認 |
| ライブ確認 | `make phase5`。kubeconfig 更新あり、CI / VERIFY_SKIP_SSH 時は全体スキップ |
| 再起動後 | `make recover`。[運用](operations.md) |
| ノード更新 | `make upgrade-safe` / `make containerd-safe`。[更新](upgrade.md) |
| Tailscale OIDC CI | `make add-runner` / `make add-runners-all`。[配布](app-delivery.md) |
| 図 | `make diagrams` は cluster-diagram.png の生成スクリプト。下記の文書図とは別 |
| root / child | [app-of-apps.yaml](../manifests/bootstrap/app-of-apps.yaml)、[一覧と図](diagrams/app-of-apps-sync-wave.md) |
| ローカル設定 | [settings.toml.example](../automation/settings.toml.example) |
| 運用状態 | [日常運用](operations.md)、[障害対応](troubleshooting.md) |
