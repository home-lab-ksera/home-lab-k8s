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

## Harbor cleanup

旧 `harbor-sandbox-image-cleanup` CronJobは撤去済みです。
`harbor-maintenance`が5分ごとに稼働版・直前正常版・候補を確認し、最大3世代へ整理します。
削除候補がある回だけHarbor標準保持を実行し、GCは毎日03:30 JSTに実行します。
容量上限、全repoの定期保守・GC、正常版への切り戻し手順は [Harborの3世代保持](harbor-retention.md) を参照してください。
標準保持処理を単独で定期実行せず、配布と保守Jobが実行を調停します。

## Renovate と監視

更新ルール・実行・PR 検証は [アプリの更新](application-auto-updates.md)、
Grafana / Loki / Alloy と通知は [監視](observability.md) を参照してください。
