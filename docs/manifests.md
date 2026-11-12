# マニフェストの配置と所有

[bootstrap/applications/kustomization.yaml](../manifests/bootstrap/applications/kustomization.yaml) から
Application の `source` を辿ったものが通常の GitOps 管理対象です。
ディレクトリの存在や上位の集約 Kustomization だけで、稼働時の所有者を判断しないでください。

| 配置 | 責務 |
|---|---|
| [bootstrap/](../manifests/bootstrap/) | root と child Application。core / infrastructure / platform / access / user-apps に分類 |
| [core/](../manifests/core/) | Namespace、基本 NetworkPolicy、StorageClass |
| [infrastructure/](../manifests/infrastructure/) | ネットワーク・証明書・ストレージコントローラなど |
| [platform/](../manifests/platform/) | ArgoCD、ESO、Harbor、RustFS、PGO、共通 DB、共有設定、監視 |
| [access/](../manifests/access/) | Gateway、HTTPRoute、DNS |
| [apps/](../manifests/apps/) | アプリの Deployment / Service / CronJob など |
| [contracts/](../manifests/contracts/) | 非機密な環境値と公開経路の照合用定義 |

## 追加・変更時の規約

- アプリ実体と `bootstrap/applications/user-apps/<app>-app.yaml` をセットで追加し、親の Kustomization に登録します。
- HTTPRoute など接続系は `access/` に分離し、新しい所有者なら access Application も登録します。
- ローカル Git source は `targetRevision: HEAD`。外部 chart の revision は chart の固定版です。
- cert-manager の Issuer / Certificate は [security/cert-manager](../manifests/infrastructure/security/cert-manager/) に集約します。
  SecretStore / ExternalSecret は [platform/secrets/external-secrets](../manifests/platform/secrets/external-secrets/) に置きます。
- `core/` に Application を追加しません。同一リソースを別 Application から重複管理しません。
- Harbor / RustFS / PGO は repo-local Helm wrapper と同梱 chart を持ち、描画に `kustomize build --enable-helm` を使います。
- `contracts/` は生成器ではありません。値の変更時は参照する YAML / スクリプトも更新し、整合性検証を実行します。

`argocd-projects` と `argocd-core` は同じ source directory を使いますが、前者は `directory.include` で
AppProject ファイルのみ、後者は Kustomize で本体・設定のみを描画します。

## ストレージ

| StorageClass | 宣言元 | 注意点 |
|---|---|---|
| `local-path` | [local-path](../manifests/infrastructure/storage/local-path/) | ノードローカル。Grafana / Loki / Alloy / PostgreSQL（DB 本体）などで使用 |
| `nfs-external` | [NFS Application](../manifests/bootstrap/applications/infrastructure/nfs-subdir-external-provisioner.yaml) | 動的作成、Delete、archiveOnDelete。共有 NFS を使用 |
| `nfs-postgresql` | [core](../manifests/core/storage-classes/nfs-postgresql.yaml) | 同じ NFS provisioner、Retain、pgBackRest repo1 用（DB 本体は local-path） |

要求容量は NFS ディレクトリごとの上限や予約を保証しません。Retain も別媒体へのバックアップではありません。
[core/kustomization.yaml](../manifests/core/kustomization.yaml) には `nfs-postgresql` が登録されています。
未使用だった no-provisioner の `local-storage` / `local-ssd-storage` は 2026-11-10 に撤去しました。

配置検証の根拠は [gitops-check.py](../automation/scripts/ci/gitops-check.py)、
[policy-check.py](../automation/scripts/ci/policy-check.py)、[contract-check.py](../automation/scripts/ci/contract-check.py) です。
