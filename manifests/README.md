# Kubernetes マニフェスト

配置の規約と Application の一覧は [GitOps](../docs/gitops.md) を参照してください。
管理対象かどうかは、ディレクトリの有無ではなく Application の source から判断します。

```bash
# リポジトリのルートで、適用せずに描画を確認
kustomize build manifests/core
kustomize build --enable-helm manifests/platform/harbor
```

- [アプリの追加と配布](../docs/app-delivery.md)
- [アクセス経路](../docs/access.md)
- [Secret 管理](../docs/secrets.md)
- [検証と CI](../docs/validation.md)
