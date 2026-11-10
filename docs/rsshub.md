# RSSHub

[アプリ宣言](../manifests/apps/rsshub/) と [access 宣言](../manifests/access/rsshub/) がこのリポジトリの管理範囲です。

| 項目 | 宣言 |
|---|---|
| Application | `rsshub` / `rsshub-access` |
| Namespace / Deployment | `sandbox` / `rsshub-private` |
| Service | `rsshub:1200` |
| image | `harbor.qroksera.com/sandbox/rsshub-private:latest`、Always pull |
| URL | `https://rsshub.internal.qroksera.com` |
| アプリ repo | `home-lab-ksera/RSSHub-private`（GitHub-hosted runner + Tailscale OIDC） |

## 実行環境

RSSHub は Redis cache を使い、Chromium 用 `/dev/shm` に 256Mi の memory emptyDir を割り当てます。
`rsshub-redis` は `maxmemory 128mb` / allkeys-lru、RDB / AOF を無効にした再生成可能なキャッシュです。
Pod 再作成でキャッシュは失われます。外部サイトの認証が必要な route には別途 ExternalSecret の設定が必要です。

## 配布

生成器の制約は [アプリ配布](app-delivery.md) を参照してください。
`RSSHub-private` は大文字を含むため Docker 名の小文字化が必要です。
アプリ側で Chromium を含む build、対象アーキテクチャ、KUBECONFIG 引き継ぎ、認証の渡し方を確認します。
このリポジトリにはアプリ側 workflow の追跡済み正本がないため、その配備済み内容や実行成功はここでは断定しません。

## 確認

```bash
kubectl -n argocd get applications rsshub rsshub-access
kubectl -n sandbox rollout status deployment/rsshub-private --timeout=300s
kubectl -n sandbox logs deployment/rsshub-private --tail=50
curl --fail https://rsshub.internal.qroksera.com/healthz
```

内部 DNS の準備前は、別ターミナルで Service を転送して healthz を確認できます。

```bash
kubectl -n sandbox port-forward service/rsshub 1200:1200
# 別ターミナル
curl --fail http://localhost:1200/healthz
```

healthz に加え、実際に利用するフィードとブラウザ依存 route も確認します。
