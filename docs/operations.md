# 日常運用

既存 kubeconfig で対象クラスタを確認してから読み取りを始めます。

```bash
kubectl config current-context
kubectl get nodes -o wide
kubectl -n argocd get applications
kubectl get pods -A
kubectl get events -A --sort-by='.lastTimestamp'
```

期待する状態は Node Ready、Application Synced / Healthy、必要な workload の Ready です。
Job の Completed は正常終了として扱い、Pod の Running 表示だけで readiness を判断しません。
`make phase5` は kubeconfig の上書きと Application refresh を含むため、動作は [bootstrap](bootstrap.md) を確認してください。

## 定期的に確認する項目

| 項目 | 確認先 |
|---|---|
| アラート・ログ・容量 | [Grafana / VMUI と監視](observability.md) |
| Secret 同期 | `kubectl get externalsecrets -A`、`kubectl get clustersecretstores` |
| 公開・TLS | `kubectl get gateway,httproute -A`、`kubectl get certificate -A` |
| データ領域 | `kubectl get pv`、`kubectl get pvc -A`、ホストの NFS / ディスク空き容量 |
| DB バックアップ | [pgBackRest の成功・復元確認](shared-postgresql.md#バックアップ) |
| RustFS / Harbor の resources | [観測値・設定の根拠・反映後の確認](resource-sizing.md) |
| worker の一時領域 | [ディスク拡張と容量監視](worker-disk.md)、Grafana「03 ストレージ」のノード容量 |

## CronJob / Job

```bash
kubectl get cronjobs,jobs -A
kubectl -n <namespace> describe job <job-name>
kubectl -n <namespace> get pods -l job-name=<job-name>
kubectl -n <namespace> logs job/<job-name>
```

定期処理には PostgreSQL 資格情報反映、監視の週次通知、Harbor のイメージ削除、アプリのバッチがあります。
RustFS lifecycle は CronJob ではなく PostSync Job です。
手動再実行は通知・削除・書き込みを再度起こすため、対象の command と入力を確認してから行います。
Pod 削除後の kubectl ログは取得できません。Alloy が収集済みなら保持期間内の Loki を調べます。

## ホスト再起動後

```bash
make recover
```

[recover-after-reboot.sh](../automation/scripts/recover-after-reboot.sh) は、既定で `k8s-` から始まる VM を起動し、
ローカル kubeconfig で API 応答・Node Ready を待ちます。kubeconfig の取得や VM の再作成は行いません。
`virsh` / `kubectl` / SSH が利用できるユーザーで実行します。

```bash
RECOVER_MAX_WAIT_SECONDS=600 RECOVER_CHECK_INTERVAL_SECONDS=15 make recover
```

Node Ready の待機失敗を許容して後続へ進むため、完了メッセージだけで正常と判断せず、Application とデータサービスを確認します。
再構築用の `make all` / `make phase2` は復旧の代替ではありません。

## ノード保守

バックアップと workload の配置、local-path PVC、emptyDir、PDB を確認し、1 台ずつ行います。

```bash
kubectl drain <node-name> --ignore-daemonsets
# OS / runtime の保守とノード状態の確認
kubectl uncordon <node-name>
```

drain が停止した場合は原因を確認します。データを破棄するオプションを機械的に追加しないでください。
単一 replica やノード固定の volume では停止が発生します。更新入口は [ノード更新](upgrade.md) を参照してください。
