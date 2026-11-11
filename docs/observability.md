# 監視・ログ・通知

[observability Kustomization](../manifests/platform/observability/kustomization.yaml) が構成の正本です。
データ収集・評価・通知・表示は `observability` Application、HTTPRoute は `observability-access` が管理します。

## 構成と保存先

| コンポーネント | 役割 | 保存 |
|---|---|---|
| VictoriaMetrics | metrics 収集・保存、VMUI | nfs-external 20Gi、保持 30 日 |
| kube-state-metrics | Kubernetes / ArgoCD / ESO / Gateway / HTTPRoute / Certificate の状態 | metrics を出力 |
| vmalert | 30 秒間隔のルール評価 | 状態は VictoriaMetrics に保存・復元 |
| Alertmanager | 集約・Slack 通知 | emptyDir。silence / 通知履歴は Pod 再作成で消失 |
| Grafana | ダッシュボード、metrics / logs / SQL の探索 | local-path 2Gi |
| Loki | Pod ログ保存 | local-path 20Gi、filesystem、保持 168 時間 |
| Alloy | Kubernetes API 経由の Pod ログ収集・転送 | local-path 1Gi に WAL |

local-path はノードローカルの単一コピーです。PVC の要求サイズや保持期間は実ディスクの容量制限ではありません。
保存先の障害や監視基盤全体の停止を、この構成だけで外部から監視する仕組みはありません。

## 画面

- Grafana: `https://grafana.internal.qroksera.com`
- VMUI: `https://observability.internal.qroksera.com/vmui/`

どちらも内部経路です。DNS / Tailnet の設定は [access](access.md) を参照してください。

| Grafana のダッシュボード | 内容 |
|---|---|
| 01 全体概要 | Node / Pod / ArgoCD、CPU・メモリ、requests |
| 02 アプリ・ログ | workload、再起動、CronJob、Pod ログ |
| 03 ストレージ | PVC、容量と未取得対象 |
| 04 PostgreSQL / SQL | registry の現在の SQL 統計と Explore |

データソース・画面の正本は [config/](../manifests/platform/observability/config/) です。
Git 管理の画面を変更する場合は JSON に反映します。SQL は [DB の権限](shared-postgresql.md#grafana-から-sql-を使う) を確認します。

「02 アプリ・ログ」は「アプリ / CronJob」で名前を選ぶと、配下の全 Pod・全コンテナのログを
1つのパネルに時刻順で表示します。種類や Pod 名の選択は不要です。
例えば `coredns` は各 replica、`hitomi-monitor` は選択期間内の各実行のログをまとめます。
Namespace は任意の追加絞り込みです。ログ詳細から元の Pod / コンテナを確認できます。
CPU・メモリの内訳表示は Pod 単位で、アプリの選択に連動します。

[workload-rules.yaml](../manifests/platform/observability/config/workload-rules.yaml) で
所有関係とアプリへの所属を30秒間隔で記録します（metrics の保持は30日）。
CronJob は CronJob 名、それ以外は Pod の `app.kubernetes.io/name` → `app` → 所有者名の順に使います。
同じアプリラベルを持つ複数 Deployment / StatefulSet なども1つの候補にまとまります。
kube-state-metrics に公開する Pod ラベルはこの2つだけに限定します。
選択期間内に所属の記録があれば削除済み Pod も対象です。導入前や収集前に消えた Pod は
Explore で namespace / pod を直接指定します。種類・Pod の古い URL パラメーターは絞り込みに使いません。
ログ本文の追加保存や Loki ラベルの追加は行いません。ログ保持168時間・自動削除を維持します。
削除には Compactor の処理待ちと2時間の猶予があり、容量に応じた早期削除はしません。

初回管理者は `admin`、ESO Password generator が `grafana-admin` を CreatedOnce で生成します。
管理端末で必要なときだけ値を取得します。

```bash
kubectl -n observability get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d
```

この Secret は DB 初期化用です。UI で変更したパスワードと一致するとは限りません。
Secret の削除・再生成を既存ユーザーのパスワードリセットとして使いません。

## 収集範囲

[VictoriaMetrics](../manifests/platform/observability/victoria-metrics.yaml) は kube-state-metrics、
kubelet volume stats、kubelet `/metrics/resource`、annotation 付き Pod の metrics を収集します。
`/metrics/cadvisor` は60秒間隔で収集し、全ノードのルートディスク `/dev/vda1` の
`container_fs_limit_bytes` / `container_fs_usage_bytes`（`id="/"`）だけを保存します。
追加のexporterは使いません。Grafana「03 ストレージ」にノードの空き推定値・使用率を表示します。
Namespace/PVCのフィルタには依存せず、emptyDir・イメージキャッシュ・ノードログを含みます。
kubelet は各ノードの InternalIP:10250 に直接接続し、ServiceAccount とクラスタ CA で認証・検証します。
権限は `nodes/metrics` の `get` と探索用の Node / Pod 読み取りだけで、`nodes/proxy` は付与しません。
初回導入・既存クラスタの切り替えには [kubelet 証明書の準備と検証](kubelet-metrics.md) が必要です。
`kubectl top` が使う Metrics API は [metrics-server](../manifests/bootstrap/applications/infrastructure/metrics-server.yaml) が提供します。
解像度は60秒で、HPA には使っていません。同じ kubelet 証明書を検証して接続し、`--kubelet-insecure-tls` は使いません。

[Alloy](../manifests/platform/observability/config/config.alloy) は Pod ログに
namespace / pod / container / node / cluster を付けます。Grafana Explore の Loki では例えば次を検索します。

```logql
{namespace="sandbox", container="api-hub"}
```

journald・監査ログ・収集前に消えた Pod のログは対象外です。
Alloy は 167 時間より古いログを転送前に除外し、Loki は Compactor で期限切れを削除します。
長時間停止やローテーションでの無欠損収集を保証しません。

## アラート

正確な式と継続時間は [alert-rules.yaml](../manifests/platform/observability/config/alert-rules.yaml) が正本です。

| 対象 | 条件の概要 / 継続時間 |
|---|---|
| NetworkPolicy controller / metrics | 利用不能・欠損 / 5 分 |
| kube-state-metrics | scrape 失敗・欠損 / 5 分 |
| Node / Pod | NotReady / 5 分・15 分 |
| コンテナ | 15 分間に 3 回以上再起動 / 5 分 |
| Deployment / PVC | replica 不足 / 10 分、Pending・Lost / 15 分 |
| ArgoCD | sync 異常 / 15 分、health 異常 / 10 分 |
| ESO / Gateway / Certificate | Ready・Programmed が false / 10 分・10 分・15 分 |
| volume | 空き 20% 未満・10% 未満 / 15 分 |
| kubelet volume scrape | 失敗・欠損 / 5 分 |
| ノードのルートディスク | 空き推定値12GiBまたは20%未満 / 10 分、9GiBまたは15%未満 / 5 分 |
| ノードのルートディスク計測 | Node台帳にあるノードの容量・使用量が欠損、または容量が0 / 5 分 |
| pgBackRest（`PostgreSQLBackupStale`） | full / diff のどちらも26時間成功なし、またはメトリクス欠損 / 30 分 |
| pgBackRest（`PostgreSQLBackupFailed`） | CronJob の直近スケジュール実行が未成功 / 2 時間 |

condition や対象時系列そのものの欠損をすべて検出する構成ではありません。
HTTPRoute metrics はあっても現行 alert rule はありません。URL の外形監視や DB 復元成功は別途確認します。
NFS の volume stats は共有ファイルシステムの容量で、PVC 固有の使用率ではなく、PVC 間で合算できません。
ノードの空き推定値はcapacity−usageで、非rootユーザーが使える `f_bavail` と完全一致する保証はありません。
アプリ側の4GiB空き保護は維持します。rootデバイスを変更するときはscrape・alert・dashboardを併せて変更します。

## Slack と週次レポート

[Alertmanager 設定](../manifests/platform/observability/config/alertmanager.yaml) は
ESC の `alertmanager-slack-webhook-url` → Secret `alertmanager-slack` を参照します。
初回集約待ち 30 秒、グループ更新 5 分、未復旧の再通知 4 時間。発火・復旧の両方を送ります。
Secret が準備できるまで Pod は起動できません。アプリ用の Slack Bot token とは別です。

## デッドマンスイッチ

vmalert の `Watchdog` は常に発火し、Alertmanager は Slack ではなく `heartbeat` receiver へ1分ごとに送ります。
送信先は ESC の `alertmanager-heartbeat-url` → Secret `alertmanager-heartbeat`（healthchecks.io の Ping URL）です。
healthchecks.io 側で受信が途絶えると、クラスタ外から通知されます。クラスタ・ホスト・回線の停止、
vmalert / Alertmanager の停止、vmalert → Alertmanager の経路断のいずれでも途絶します。
healthchecks.io の check は Period 5 分 / Grace 5 分を目安とし、通知先は Slack やメールなどクラスタ外に依存しない経路にします。
Secret は optional でマウントするため、未作成でも Alertmanager は起動します（その間は送信のみ失敗）。
Grafana の発火中アラート表示からは `Watchdog` を除外しています。

[weekly-observability-report](../manifests/platform/observability/weekly-report.yaml) は月曜 09:00 Asia/Tokyo に
直近 7 日の状態を集計し、同じ Webhook へ送ります。Alertmanager の silence とは独立しています。
5 分刻みの状態集計と推定再起動回数で、稼働率や確定した障害件数ではありません。
欠損は正常と扱わず、PVC 容量は現在値を表示します。

送信せずに本文を確認する場合:

```bash
kubectl -n observability port-forward svc/victoria-metrics 18428:8428
# 別ターミナル
VICTORIA_METRICS_URL=http://localhost:18428 \
  python3 -B manifests/platform/observability/scripts/weekly-report.py --dry-run
```

CronJob から手動 Job を作ると実際に Slack へ送信します。再試行時は重複通知も確認してください。

## 障害時・同期後の確認

```bash
kubectl -n observability get pods,pvc,externalsecrets,cronjobs
kubectl -n argocd get applications observability observability-access
kubectl -n observability logs deployment/alloy --tail=50
kubectl -n observability logs deployment/vmalert --tail=50
kubectl -n observability logs deployment/alertmanager --tail=50
```

Alertmanager / vmalert の UI は必要に応じて個別に port-forward します。

```bash
kubectl -n observability port-forward svc/alertmanager 9093:9093
# 別ターミナルで必要なら実行
kubectl -n observability port-forward svc/vmalert 8880:8880
```

`http://localhost:9093/` と `http://localhost:8880/vmalert/` で通知・評価を確認します。
Grafana の metrics / logs / SQL 接続と直近データも確認します。CI の設定検証は [reference](reference.md) を参照してください。

アプリの非同期処理は[業務キューと録画の監視](application-queue-monitoring.md)を参照。
通知・outbox・mediaの滞留、失敗・結果不明、メトリクス欠損をPodの正常性とは別に監視する。
