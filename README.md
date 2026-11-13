# home-lab-k8s

ホームラボの Kubernetes を構築・運用するリポジトリです。
Ubuntu / libvirt / Terraform で VM と kubeadm クラスタを用意し、ArgoCD が基盤・公開経路・アプリを管理します。

## ドキュメント

[ドキュメント入口](docs/README.md) に、現行コードから確認した構成と手順をまとめています。

- [全体構成](docs/architecture.md)
- [初期構築](docs/bootstrap.md)
- [GitOps](docs/gitops.md) / [配置規約](docs/manifests.md)
- [App-of-Apps / Sync Wave 図](docs/diagrams/app-of-apps-sync-wave.md)
- [アプリと Runner の追加](docs/app-delivery.md)
- [日常運用](docs/operations.md) / [障害対応](docs/troubleshooting.md)
- [共通 PostgreSQL と復旧](docs/shared-postgresql.md)

## 構築前に

`make phase1` はホスト準備、`make phase2` は VM / Kubernetes の再作成です。
**`make phase2` とそれを含む `make all` は既存 VM・Terraform 状態を削除します。**
既存環境の通常保守や再起動後の復旧には使わず、先に [構築手順と設定の適用範囲](docs/bootstrap.md) を確認してください。

新規環境では `automation/settings.toml.example` を `automation/settings.toml` にコピーして編集します。
全フェーズの入口は [Makefile](Makefile)、実行順序の正本は [run.sh](automation/scripts/run.sh) です。

## 検証

```bash
# Docker 内の専用 toolchain で主要な静的検証
make validate
# ローカル（初回は docs/reference.md のセットアップを実施）
mise exec -- automation/scripts/ci/validate.sh
# toolchain 導入済みの場合
make validate-local
```

検証範囲、追加 CI、個別コマンドは [検証・参照](docs/reference.md) を参照してください。

アプリ簡素化後の構成は[構成図](docs/application-simplification-architecture.md)、現行操作は[アプリ運用・復元](docs/application-postgresql-cutover.md)を参照。
