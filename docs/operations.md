# 日常運用

普段は Slack の通知を待つだけで足ります。通知が来たら [障害対応](troubleshooting.md) と [監視](observability.md#アラート) を見ます。

## 日々の確認

```bash
kubectl config current-context
kubectl get nodes -o wide
kubectl -n argocd get applications
kubectl get pods -A | grep -vE 'Running|Completed'
kubectl get events -A --sort-by='.lastTimestamp' | tail -20
```

正常な状態は、Node が Ready、38 個の Application がすべて Synced / Healthy、Slack で発火中のアラートがないことです。

目的別の確認先:

| 見たいもの | コマンド・場所 |
|---|---|
| アラート、容量、ログ | Grafana（[監視](observability.md#画面)） |
| Secret の同期 | `kubectl get externalsecrets -A` |
| 経路と証明書 | `kubectl get gateway,httproute -A`、`kubectl get certificate -A` |
| ボリューム | `kubectl get pvc -A`、ホストの `df -h /mnt/k8s-storage` |
| 定期処理 | `kubectl get cronjobs,jobs -A` |
| 配布されたイメージ | [アプリ配布の確認](app-delivery.md#確認) |

## 定期処理

| CronJob | 時刻（JST） | 内容 |
|---|---|---|
| database / `postgresql-backup` | 毎日 03:00 | DB の dump |
| database / `postgresql-restore-drill` | 日曜 04:30 | 最新の dump の復元訓練 |
| observability / `weekly-observability-report` | 月曜 09:00 | 週次レポートを Slack へ |
| rustfs / `rustfs-scanner-monitor` | 15 分ごと | scanner の状態を metrics に |
| sandbox / `hitomi-monitor` | 02:55 から 4 時間ごと | 収集 |
| sandbox / `hitomi-pdf-monitor` | 00:50、07:50 | メディアの取得計画 |
| sandbox / `media-reconcile` | 毎時 20 分 | 期限の整理 |
| sandbox / `media-verify-objects` | 毎日 04:40 | S3 の実在確認 |

このほか、Harbor の保持ルール（03:00）と GC（03:30）が Harbor の中で動きます。

Job が失敗すると `KubernetesCronJobFailed` が通知します。

```bash
kubectl -n <namespace> get jobs
kubectl -n <namespace> logs job/<job>
```

手で再実行すると、通知や書き込みがもう一度起きます。何をする Job かを確かめてから実行します。

```bash
kubectl -n <namespace> create job "<name>-manual-$(date +%s)" --from=cronjob/<cronjob>
```

## 手作業が要るもの

自動化していない定期作業です。どれもアラートが知らせます。

| きっかけ | 作業 |
|---|---|
| `KubeletServingCSRPending`（年 1 回、ノードごと） | [CSR を承認する](nodes.md#kubelet-の-serving-証明書) |
| `ControlPlaneCertificateExpiring` | Kubernetes を upgrade するか、[証明書を更新する](nodes.md#control-plane-の証明書) |
| `HostRebootRequired` | ホストを再起動する（下記） |
| Renovate の PR | 確認してマージする（[更新](updates.md)） |
| 2 週間ごと | [ホストの容量を測り直す](nodes.md#容量の考え方) |

## ホストを再起動したあと

VM は libvirt の autostart で自動的に起動し、クラスタは放っておいても戻ります。戻りを待って確認するには、ホストで次を実行します。

```bash
make recover
```

[recover-after-reboot.sh](../automation/scripts/recover-after-reboot.sh) は、`k8s-` で始まる VM のうち止まっているものを起動し、API の応答と Node の Ready を待ちます。
VM を作り直すことはありません。待ち時間は環境変数で延ばせます。

```bash
RECOVER_MAX_WAIT_SECONDS=600 RECOVER_CHECK_INTERVAL_SECONDS=15 make recover
```

Node が Ready にならなくてもスクリプトは最後まで進みます。終了メッセージだけで判断せず、次を確認します。

- NFS が export されている（`sudo /usr/sbin/exportfs -v`）。export がないと、NFS を使う Pod（RustFS、Harbor、VictoriaMetrics、home-camera）が起動しません。
- 全 Application が Healthy に戻る。
- 録画中に止めた場合、`CameraPartialSegment` が出ることがあります（[home-camera](apps.md#home-camera)）。

ホストの sudo はパスワードが要り、`/usr/sbin` が PATH にありません。コマンドはフルパスで書きます。

再起動する前に、hitomi や media の Job が動いていないか見ておくと、やり直しが減ります。

## ノードを保守する

1 台ずつ行います。replica 1 のサービスはその間止まります。

```bash
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
# 作業する
kubectl uncordon <node>
```

- `k8s-worker2` を drain すると、PostgreSQL、Loki、Grafana は他のノードへ移れず、戻すまで止まります（local-path の PV がこのノードにあるため）。
- `--delete-emptydir-data` で、media worker の取得中のファイルなど emptyDir の中身は消えます。取得は再試行されます。
- drain が進まないときは理由を見ます。`--force` を機械的に足しません。

Kubernetes と containerd の版を上げる手順は [更新](updates.md#kubernetes) にあります。

## リソースを見直す

requests と memory の limit は、7 日間の実測の最大値に余裕を足して決めています。CPU の limit は、基本的に付けません。
見直すときは VMUI で実測を取ります。

```promql
max by (namespace, container) (
  max_over_time(container_memory_working_set_bytes{job="kubelet-resources", namespace="sandbox"}[7d])
) / 1024 / 1024
```

```promql
max by (namespace, container) (
  max_over_time(rate(container_cpu_usage_seconds_total{job="kubelet-resources", namespace="sandbox"}[2m])[7d:2m])
)
```

- 収集は 30 秒間隔なので、それより短い山は見えません。OOM（exit 137）が出たら、グラフに現れていなくても limit を上げます。
- 普段の小さい値だけを見て下げません。重い処理（録画の送信、大きな動画の取得、イメージの push）を含む期間で見ます。
- Harbor と RustFS は chart の values で設定します。`automation/tests/ci/test_platform_resources.py` が、全コンテナに requests と memory の limit があることを検査します。
