# home-lab-k8s

ホームラボの Kubernetes を構築・運用するリポジトリです。
Ubuntu / libvirt / Terraform で VM と kubeadm クラスタを用意し、ArgoCD が基盤・経路・アプリを管理します。

## ドキュメント

[docs/README.md](docs/README.md) が入口です。現在の構成と手順だけを載せています。

- [全体構成](docs/architecture.md) / [ホストとノード](docs/nodes.md)
- [GitOps（配置規約と Application 一覧）](docs/gitops.md) / [検証と CI](docs/validation.md)
- [アクセス経路](docs/access.md) / [Secret 管理](docs/secrets.md)
- [アプリ一覧](docs/apps.md) / [アプリ配布](docs/app-delivery.md)
- [日常運用](docs/operations.md) / [障害対応](docs/troubleshooting.md) / [更新](docs/updates.md)

## 注意

**`make phase2` と、それを含む `make all` は、既存の VM と Terraform の状態を削除して作り直します。**
通常の保守や再起動後の復旧には使いません。先に [構築と再構築](docs/bootstrap.md) を読んでください。

## 検証

```bash
make validate          # Docker 内の toolchain で CI と同じ静的検証
make validate-local    # CLI を導入済みの環境で直接実行
```
