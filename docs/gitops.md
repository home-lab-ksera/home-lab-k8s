# GitOps の反映と確認

[bootstrap-root](../manifests/bootstrap/app-of-apps.yaml) が
[child Application 定義](../manifests/bootstrap/applications/) を同期し、各 Application が実リソースを管理します。
root は `prune: true` / `selfHeal: true`。child の例外・参照先は [一覧](diagrams/app-of-apps-sync-wave.md) を確認します。

## 通常の変更

1. [配置規約](manifests.md) に従って宣言を編集します。
2. `git diff --check` と [静的検証](reference.md) を実行します。
3. 差分をレビューして Git に反映します。ローカル編集だけでは ArgoCD は取得できません。
4. 対象 Application の revision、Sync、Health と実リソースの動作を確認します。

```bash
kubectl -n argocd get applications
kubectl -n argocd describe application <app-name>
kubectl -n argocd get application <app-name> \
  -o jsonpath='{.status.sync.revision}{"\n"}{.status.sync.status}{" / "}{.status.health.status}{"\n"}'
```

`HEAD` はこのリポジトリを参照する bootstrap Application の規約です。外部 Helm chart は固定版を指定します。
Application の削除は finalizer / prune を介して配下の削除につながるため、PVC や DB を含む場合は保持設定も確認します。
`argocd-projects` と `argocd-core` は `prune: false`。上流から消えたリソースは別途削除判断が必要です。

## 適用順序と依存関係

sync-wave は **その同期操作で扱うリソースの順序**です。root の wave と各 child の独立した自動同期、
ApplicationSet が後から生成する Application の wave を、単一の全クラスタ依存解決として扱いません。

CRD → Custom Resource、ESO → Secret → Pod、PGO → DB → Harbor などは、各 controller の Ready と実動作を確認します。
cert-manager と config は同じ wave 7、証明書が使う ESO 定義は wave 9 なので、一時的な未準備は起こり得ます。
図と全 Application の source は [Sync Wave](diagrams/app-of-apps-sync-wave.md) を参照してください。

## ArgoCD 本体・通知

本体と CRD は [upstream](../manifests/platform/argocd-config/upstream/kustomization.yaml)、
ローカル設定は [argocd-config](../manifests/platform/argocd-config/kustomization.yaml) の patch が正本です。
通常の版上げに bootstrap の再実行は不要です。

[Notifications 設定](../manifests/platform/argocd-config/argocd-notifications.yaml) と
[apps Project の購読](../manifests/platform/argocd-config/argocd-projects.yaml) により、
同期成功かつ Synced / Healthy、および同期操作の Error / Failed を Slack に通知します。
成功通知の revision はデプロイリポジトリの同期 SHA で、アプリソースや `latest` の digest ではありません。
継続的な異常は [監視](observability.md) が扱います。

Webhook は ESO が `argocd-notifications-secret` に Merge します。

```bash
kubectl -n argocd get externalsecret argocd-notifications-secret
kubectl -n argocd logs deployment/argocd-notifications-controller --since=10m
```

## bootstrap時のArgoCDアクセス

旧 `http://argocd.local` のHTTPRouteは使用しません。bootstrapはこの経路を作成せず、
通常の管理画面は `https://argocd.internal.qroksera.com` または既存の外部HTTPS URLを使います。
Gatewayの準備前に必要な場合は、踏み台のkubectl port-forwardをSSHでlocalhostへ転送します。

```bash
ssh -i ~/.ssh/id_ed25519 -L 127.0.0.1:8080:127.0.0.1:8080 ksera@192.168.10.11 \
  'kubectl -n argocd port-forward --address=127.0.0.1 svc/argocd-server 8080:80'
```

このセッション中だけ `http://127.0.0.1:8080` でアクセスできます。終了すると転送も停止します。
旧RouteはGitOps管理外のため、宣言の削除だけでは消えません。移行時には正規HTTPSの疎通を確認した後、
`argocd/argocd-server` HTTPRouteだけを削除し、他のArgoCD ServiceやRouteは維持します。

## 切り戻し

通常は Git の変更を revert して同期を確認します。DB migration やデータ形式の変更後は、
manifest の巻き戻しだけでデータが戻るとは扱いません。
緊急の手動修正では selfHeal による差し戻しを考慮し、最終状態を Git に反映して自動同期を戻します。
現行 sandbox の可変タグ配布には Runner による再起動経路があるため、[アプリ配布](app-delivery.md) も参照してください。
