# プラットフォームの補助機能

## Reloader

[reloader/manifest.yaml](../manifests/platform/reloader/manifest.yaml) は kube-system に配置し、
`sandbox,observability` のみ監視します。Secret / ConfigMap の変更で annotation を付けた workload を更新し、Job / CronJob は除外します。

| 対象 | 追従する設定 |
|---|---|
| api-hub | auto annotation による参照設定 |
| Alertmanager | `alertmanager-slack`、`alertmanager-heartbeat` |
| Grafana | `grafana-postgresql`、`shared-postgresql-ca` |

Grafana の管理者初期 Secret は自動再起動対象ではありません。
Harbor の patch に Reloader annotation はありますが、**監視 namespace と RBAC に harbor がないため、自動反映は期待できません**。

annotations 戦略で Pod template に書かれる `reloader.stakater.com/last-reloaded-from` は、
対象 Application の ignoreDifferences / RespectIgnoreDifferences で扱います。
対象を増やす際は workload annotation、監視範囲、RBAC、Application の差分設定を揃えます。

## RustFS lifecycle

[RustFS Kustomization](../manifests/platform/rustfs/kustomization.yaml) は chart に加えて
PostSync の `rustfs-lifecycle` Job を持ちます。
[buckets.txt](../manifests/platform/rustfs/buckets.txt)、[lifecycle.json](../manifests/platform/rustfs/lifecycle.json)、
[apply-lifecycle.sh](../manifests/platform/rustfs/apply-lifecycle.sh) が対象と処理の正本です。

専用 NetworkPolicy が同 namespace の Job から S3:9000 を許可します。
同期時には Job の成功と各対象バケットの lifecycle を確認します。保持設定の変更はオブジェクトの削除につながるため、
対象バケットと日数をレビューします。

### scanner の監視と復旧

lifecycle の期限切れ削除は RustFS の scanner が実行します。設定の適用（上記 Job の成功）だけでは削除は保証されません。
2026-11-03 以前から 11-10 まで、scanner は `usage_floor_load_failed` で停止し続け、一度も走査を完了していませんでした。
そのため30日を超えたオブジェクトが残っていました。

[rustfs-scanner-monitor](../manifests/platform/rustfs/scanner-monitor.yaml) CronJob が15分ごとに管理 API
（`/rustfs/admin/v3/scanner/status`、`/rustfs/admin/v3/ilm/expiry/status`）を読み、VictoriaMetrics へ送信します。
通常は約10分ごとに走査が完了します。`RustFSScannerStalled`（6時間完了なし）と
`RustFSScannerMonitorMissing`（監視の送信停止）で通知します。

scanner status の `cycle_recovery.classification` が `usage_floor_load_failed` の場合は、使用量状態を再構築します。
オブジェクトは削除せず、次の完全な走査で使用量を作り直します。走査が再開すると期限切れのオブジェクトがまとめて削除されます。

```bash
kubectl -n rustfs port-forward svc/rustfs-svc 19000:9000 &
AK=$(kubectl -n rustfs get secret rustfs-auth -o jsonpath='{.data.RUSTFS_ACCESS_KEY}' | base64 -d)
SK=$(kubectl -n rustfs get secret rustfs-auth -o jsonpath='{.data.RUSTFS_SECRET_KEY}' | base64 -d)
curl -s --aws-sigv4 "aws:amz:us-east-1:s3" --user "$AK:$SK" localhost:19000/rustfs/admin/v3/scanner/status | jq '.freshness, .cycle_recovery'
curl -s --aws-sigv4 "aws:amz:us-east-1:s3" --user "$AK:$SK" -X POST -H 'Content-Type: application/json' \
  -d '{"mode":"full-rebuild"}' localhost:19000/rustfs/admin/v3/scanner/usage-state/reset
```

手順の正本は RustFS の `docs/operations/scanner-runtime-controls.md`（採用版のタグ）です。

## Harbor cleanup

旧 `harbor-sandbox-image-cleanup` CronJobは撤去済みです。
`harbor-maintenance`が5分ごとに稼働版・直前正常版・候補を確認し、最大3世代へ整理します。
削除候補がある回だけHarbor標準保持を実行し、GCは毎日03:30 JSTに実行します。
容量上限、全repoの定期保守・GC、正常版への切り戻し手順は [Harborの3世代保持](harbor-retention.md) を参照してください。
標準保持処理を単独で定期実行せず、配布と保守Jobが実行を調停します。

## Renovate と監視

更新ルール・実行・PR 検証は [アプリの更新](application-auto-updates.md)、
Grafana / Loki / Alloy と通知は [監視](observability.md) を参照してください。
