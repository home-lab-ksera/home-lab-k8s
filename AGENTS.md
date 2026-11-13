# AGENTS.md

## 前提
- 応答・コードコメントは日本語を優先。
- 実行フローの正は `Makefile` と `automation/scripts/run.sh`。`automation/*/README.md` には旧手順が残るため、挙動確認はスクリプトを優先する。
- GitOps が正。Kubernetes リソース変更は原則 `manifests/`、`automation/` はローカル bootstrap / 運用スクリプト用。
- `tasks/` 配下には構造改革の target-state 計画があり、未実装の path / owner 名が含まれる場合がある。実装や挙動確認では現行 repo の実体を優先し、改革タスク時のみ `tasks/` の target-state 文脈を使う。

## 重要パス
- 現行 bootstrap 入口: `make bootstrap`（実体: `automation/scripts/run.sh bootstrap` -> `automation/platform/platform-deploy.sh`）。root `Application` は `manifests/bootstrap/app-of-apps.yaml`。
- 配置規約: `docs/manifests.md`
- App-of-Apps / Sync Wave 図: `docs/diagrams/app-of-apps-sync-wave.md`
- GitOps 運用: `docs/gitops.md`
- bootstrap / 検証: `docs/bootstrap.md`
- ArgoCD 設定: `manifests/platform/argocd-config/`
- 現行ユーザーアプリ定義: `manifests/bootstrap/applications/user-apps/*.yaml`
- 現行実アプリ: `manifests/apps/<app>/`
- 改善計画: `tasks/`（target-state を含む。現行の実体は repo を優先）

## マニフェスト規約
- `bootstrap/` には Root/Application 定義のみを置き、`core/` に ArgoCD `Application` を置かない。
- 現行 repo に新規アプリを追加する場合は `manifests/apps/<app>/` と `manifests/bootstrap/applications/user-apps/<app>-app.yaml` をセットで追加する。
- `tasks/` の改革スコープでは `apps/` workload-only、公開/接続系は `access/` へ分離する target-state を計画している。未実装の path を live repo の既成事実として扱わない。
- cert-manager 系リソースは `manifests/infrastructure/security/cert-manager/` に集約し、Gateway 配下は Gateway/HTTPRoute/TLSPolicy などルーティング系のみ置く。
- ExternalSecret 定義は `manifests/platform/secrets/external-secrets/` に集約する。
- `.sh` からの `kubectl apply` はブートストラップ最小限のみ。最終状態は ArgoCD 管理に戻す。
- `app-of-apps.yaml` や sync wave を変えたら `docs/diagrams/app-of-apps-sync-wave.md` も更新する。

## 実行コマンド
- 初期設定: `cp automation/settings.toml.example automation/settings.toml`
- 全体/個別: `make all`, `make phase1`, `make phase2`, `make bootstrap`, `make phase5`。
- 保守: `make recover`, `make upgrade-safe`, `make containerd-safe`
- `make add-runner REPO=<name>`（一括は `make add-runners-all`）は GitHub-hosted runner + Tailscale OIDC の workflow を生成する。GitHub API で公開範囲・既定ブランチを取得し、private repo は4つの repository Secrets を非表示入力または環境変数から登録する。生成されるのは共通 workflow `.github/workflows/publish-harbor.yml` を呼ぶ雛形で、各アプリリポジトリに commit する（このリポジトリでは追跡しない）。クラスタ内 runner（ARC）は撤去済みで、ScaleSet / runner group 設定は不要。
- `make all` と `make phase1` は `sudo` 前提。`make all` は `phase1 -> phase2 -> bootstrap -> phase5` の順に実行する。実行ログは `automation/run.log`。

## 検証
- CI 相当: `automation/scripts/ci/validate.sh`
- `validate.sh` は actionlint、setup / version update / 監視レポート / GitOps 検証の回帰テスト、shellcheck、yamllint、GitOps 参照・登録チェック、policy check、全 `manifests/**/kustomization.yaml` の Kustomize + kubeconform、consistency check を実行する。
- `automation/scripts/ci/gitops-check.py` はローカル Application の HEAD / source path / directory.include と、アプリの所有定義・bootstrap Kustomize への登録漏れを検証する。directory.include は現行構成の単一ファイル指定のみ対応。
- 個別確認: `shellcheck -S error -x automation/scripts/<file>.sh`, `yamllint -f parsable -c .yamllint.yml manifests/<dir-or-file>`, `kustomize build manifests/<kustomize-dir>`
- `make phase5` は既定で `k8suser@192.168.122.10` に SSH し、`/etc/kubernetes/admin.conf` をローカル `~/.kube/config` に同期してから検証する。CI または `VERIFY_SKIP_SSH=true` ではライブ検証をスキップして終了する。

## CIで落ちる既知条件
- `manifests/bootstrap/**` の `targetRevision` は `HEAD` を維持する。`main` は不可。
- `.github/workflows/weekly-version-audit.yml` と `automation/scripts/ci/version-audit.py` は bootstrap Application 名と一部マニフェストパスをハードコードしている。名前変更や移動時は同時に更新する。
