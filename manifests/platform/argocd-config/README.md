# ArgoCD Configuration (GitOps管理)

このディレクトリはArgoCD関連の設定をGitOps経由で管理します。

## 含まれるリソース

### 1. ArgoCD ConfigMaps
- `argocd-cm`: ArgoCD基本設定とGitHub OAuth設定
- `argocd-rbac-cm`: RBAC権限設定
- ソース: `argocd-config.yaml`

### 1.5. ArgoCD AppProject
- `argocd-projects.yaml`: Project境界（core/infrastructure/platform/apps）
- 同期順序の都合で `argocd-projects` Application から先に適用される

### 2. ArgoCD GitHub OAuth Secret
- `argocd-github-oauth-secret.yaml`: GitHub OAuth client secretをExternal Secrets経由で取得
- Pulumi ESCの`argocd.client-secret`キーから取得
- 既存の`argocd-secret`にマージ

### 3. Harbor Docker Registry Secrets
- `harbor-docker-registry-secrets.yaml`: 各namespace用のDocker認証情報
- 対象namespace: default, sandbox, production, staging
- Pulumi ESCの`harbor`キーから認証情報を取得

## デプロイ順序

1. **External Secrets Operator** (sync-wave="8")
   - ClusterSecretStore作成
   - Pulumi Access Token設定

2. **Platform Application** (sync-wave="9")
   - ArgoCD ConfigMaps適用
   - External Secrets作成・同期
   - Harbor認証Secret作成

3. **Harbor Application** (sync-wave="10")
   - Harbor起動後にSecretが利用可能

## 重要な依存関係

- **ArgoCD ConfigMap**: ArgoCDの再起動が必要な場合があります
- **Harbor Secrets**: Harborデプロイ前に作成されますが、namespaceが存在する必要があります
- **External Secrets**: **Pulumi Access Tokenが必須です** - 設定されていない場合はデプロイが失敗します

## トラブルシューティング

### External Secretが同期されない場合
```bash
# ClusterSecretStore確認
kubectl get clustersecretstore pulumi-esc-store

# External Secret状態確認
kubectl get externalsecrets -A

# Pulumi Access Token確認
kubectl get secret pulumi-esc-token -n external-secrets-system
```

### ArgoCD OAuth設定が反映されない場合
```bash
# ArgoCD再起動
kubectl rollout restart deployment argocd-server -n argocd

# Secret確認
kubectl get secret argocd-secret -n argocd -o jsonpath='{.data.dex\.github\.clientSecret}' | base64 -d
```
