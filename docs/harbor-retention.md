# Harborの自動保守と3世代保持

Harborは維持し、`harbor/harbor-maintenance` CronJobが10分ごとに配布と保持を調停します。
`latest` から `deploy` への昇格もこのJobが行うため、CI公開から配布開始までは最大10分待ちます。
常駐Podは追加しません。実装は [maintenance.py](../manifests/platform/harbor/scripts/maintenance.py)、
対象・切り戻し設定は [maintenance.json](../manifests/platform/harbor/maintenance.json) です。

## 保持契約

アプリのrepositoryごとに異なるdigestを最大3世代へ収束させます。同じdigestの複数タグは1世代です。

| 役割 | タグ | 更新条件 |
|---|---|---|
| 現在の正常版 | `retain-current` | ArgoCD Synced/Healthy、controller反映済み、全Pod Ready・imageID一致 |
| 直前の正常版 | `retain-previous` | 次の版の成功確認後。初期の前版が不明なら推測しない |
| 配布候補 | `latest` → `deploy` | default branch HEADをCIが公開し、前の配布が正常になってから昇格 |

CIは`build-<run-id>-<attempt>-<sha>`をpushし、HEAD照合後に`latest`へ昇格します。
Image Updaterは`deploy`だけを追い、digestをGitへ書き戻します。保守Jobが`deploy`を進めるため、
CIの連続公開と削除が重なっても、配布中の版を保護できます。公開直後の未昇格artifactは最新push 1件のルールでも保護します。

3世代は**正常に保持処理を完了した安定状態**の上限です。公開・配布中、失敗中、GC待ちには一時的に超過します。
保護役割と最新pushが4つ以上のdigestになる場合は、そのrepoの削除を見送り、正常収束後に再開します。
失敗した候補は次の公開で自動的に追い越しません。修正時は下記の切り戻しを行い、正常化後に再開します。
古いJobが動作中、Pod終了中、digest未固定、未知のartifact、APIエラーの場合も削除しません。
停止が続けば監視通知します。容量不足を理由に稼働版を削除したりquotaを広げたりしません。

対象はapi-hub、blog、cooklog、hitomi、hitomi-pdf-batch、hitomi-upload-viewer、home-camera、rsshub-privateです。
OCI indexは親digestを1世代と数え、子manifestのimageIDを照合します。
再pushで圧縮だけ異なるmanifestが増えると、containerdは同じconfigのimageに複数のrepoDigestを持ち、
PodのimageIDに旧digestを返すことがあります。digestが一致しない場合はHarborからconfig digestを取得し、
同一imageであれば一致と判定します（2026-11-10、hitomi-pdf-batchの誤検知への対応）。
CronJobと常駐workloadが同じイメージを使う場合、正常判定は常駐workloadのReadyと全templateの一致を使用します。
これは業務処理の成功を保証する検査ではありません。実行中の旧Jobは保護し、終了済みJobの履歴は保持理由にしません。
CronJobだけのアプリを追加した場合は正常判定の追加が必要で、現実装は削除を止めます。
未使用のslack.rsは参照がないことを確認したarchive指定で、最新push 3件を保持します。
未知repoは全保持し、repo単位の保留（`HarborRepositoryBlocked`）として通知します。
新アプリはImage Updaterとmaintenance.jsonを一緒に登録してください。Image Updater対象外のrepoは`"preserve": true`で登録します。

脆弱性スキャンは Harbor ではなく、各アプリの公開 workflow が push 前のイメージに対して行います。
Harbor の UI には脆弱性情報を表示しません（2026-11-11 に Trivy を無効化）。

## 自動処理と容量

1. Leaseと`concurrencyPolicy: Forbid`で多重実行を防止。前回のHarbor保持処理が実行中ならタグを変更しません。
2. Kubernetesを読み取り、正常版を確認。前版→現在版→配布タグの順で更新し、途中終了後も再試行できます。
3. Harbor標準保持ルールを更新し、削除候補がある回だけ標準dry run成功後に標準保持を実行します。
   候補がない回は状態確認だけで終了し、不要なHarbor Job履歴を増やしません。独自のartifact削除ループは使いません。
   判定不能repoは全保持。他repoの保守は進めます。保持処理の単独スケジュールは設定しません。
4. Harbor標準GCを毎日03:30 JSTに設定・修復します。`delete_untagged=false`、workers=1。
   未タグartifactをGC独自判断で消さず、保持処理が削除したblobを回収します。新規uploadの2時間の保護窓を維持します。

sandbox quotaは **4 GiB (4,294,967,296 bytes)**。到達時はpushが失敗します。
quotaはprojectの論理容量で、共有NFSの物理上限ではありません。不要blobは日次GCまで残ることがあります。
Jobはrequests 25m CPU/32Mi、limits 250m CPU/128Mi、最大15分、再試行は次の定期実行で行います。
中断時のLeaseは最大1,000秒で期限切れになります。
既存のHarbor管理Secretをread-only mountし、Kubernetes権限はworkload/ArgoCDの読取と専用Leaseの更新に限定します。
RunnerにKubernetes権限は追加しません。

監視は2段階です。

| アラート | 対象 | 条件 |
|---|---|---|
| `HarborMaintenanceStalled` | 保守全体 | Jobが30分以上成功していない（10分継続） |
| `HarborRepositoryBlocked` | repo単位 | 同じrepoの保留が1時間以上継続 |

Jobを失敗にするのは全体の異常に限ります。対象はHarbor/Kubernetes APIエラー、保持処理の失敗、
GCの実成功が36時間以上ない場合、quota使用量90%以上、メトリクス送信失敗です。
repoの保留（blocked）はJobを失敗させません。保留中のrepoは保持処理の対象外（全保持）となり、他repoの保守は継続します。

Jobは毎回VictoriaMetricsへ次を送信します（NetworkPolicyでVictoriaMetrics:8428への通信のみ許可）。

- `harbor_maintenance_repository_blocked{repository, reason}`: 保留中1、正常・preserve・archive 0。reasonは保留理由
- `harbor_maintenance_last_run_timestamp_seconds`: 最終実行時刻

```promql
max by (repository, reason) (max_over_time(harbor_maintenance_repository_blocked[15m])) == 1
```

## 状態確認・切り戻し

```bash
kubectl -n harbor get cronjob harbor-maintenance
kubectl -n harbor get jobs --sort-by=.metadata.creationTimestamp
kubectl -n harbor logs job/<直近のharbor-maintenanceのJob名>
```

ログの`eligible`・`blocked`、正常digest、役割、保持execution IDを確認します。
`HarborRepositoryBlocked`の場合は、該当repoの`reason`をログかメトリクスで確認します。
失敗時はAPI疎通、Application同期、Pod、旧Job、quota、Harbor保持/GC履歴を確認します。
手動でタグ・保持policyを変更したり旧cleanupを復活させたりしないでください。

切り戻しは`maintenance.json`の対象repoに`"rollbackDigest": "sha256:..."`を追加してGitへ反映します。
値は`retain-current`または`retain-previous`の既知の正常版に限ります。
定期Jobは失敗中でも`deploy`を指定版へ戻し、Image Updater→Git→ArgoCD経由で配布します。
設定がある間はそのdigestに固定するため、不良な`latest`へ戻りません。
正常化と修正版の公開を確認したら`rollbackDigest`をGitから削除して自動昇格を再開します。
KustomizationだけをrevertするとImage Updaterが戻してしまうため、この設定を使ってください。

緊急停止はCronJobの`spec.suspend: true`をGitへ反映します。既存Jobは終了を待ちます。
GCは別スケジュールなので、停止する必要がある場合はHarbor側でも停止します。
復旧後はsuspendを解除し、次回Jobの成功を確認します。
旧手動pilotツールは自動保守と競合するため撤去しました。

## 検証記録

状態遷移・OCI index・実行中Job・失敗・切り戻しは
[回帰テスト](../automation/tests/ci/test_harbor_maintenance.py)で検証します。
実環境の反映と検証結果は [反映記録](archive/harbor-retention-20261028.md) に追記します。
