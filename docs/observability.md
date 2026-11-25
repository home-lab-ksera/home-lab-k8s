# 監視

構成の正本は [observability/](../manifests/platform/observability/) です。すべて `observability` namespace の 1 つの Application で管理します。

## 構成

| コンポーネント | 役割 | データ |
|---|---|---|
| VictoriaMetrics | メトリクスの収集と保存、VMUI | NFS、30 日 |
| vmalert | 30 秒ごとにルールを評価 | 状態は VictoriaMetrics に保存 |
| Alertmanager | 通知の集約と送信 | なし（silence は Pod の再作成で消える） |
| kube-state-metrics | Kubernetes、ArgoCD、ESO、Gateway、Certificate の状態 | なし |
| metrics-server | `kubectl top` | なし |
| Grafana | ダッシュボード、探索 | local-path（内部 DB） |
| Loki | `sandbox` の Pod ログ | local-path、7 日 |
| Alloy | ログの収集と転送、外形監視 | local-path（送信待ち） |

## 画面

- Grafana: `https://grafana.internal.qroksera.com`
- VMUI: `https://observability.internal.qroksera.com/vmui/`

| ダッシュボード | 内容 |
|---|---|
| 01 全体概要 | Node、Pod、ArgoCD、CPU とメモリ、requests |
| 02 アプリ・ログ | アプリや CronJob を選ぶと、配下の全 Pod のログと CPU・メモリを表示 |
| 03 ストレージ | PVC の容量、ノードのディスク |
| 04 PostgreSQL / SQL | registry DB の統計と SQL の実行 |
| 05 株価 / 銘柄 | market DB。指数と銘柄を選び、終値（移動平均）・出来高・直近 15 営業日・MVP 指標・組み入れ履歴を表示。下の表で指数全体の MVP 指標を一覧 |

ダッシュボードとデータソースは [config/](../manifests/platform/observability/config/) の JSON / YAML が正本です。UI で変えても、Pod の再作成で Git の内容に戻ります。

Grafana の初回の管理者は `admin` で、パスワードは Secret にあります（UI で変えたあとは一致しません）。

```bash
kubectl -n observability get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d
```

Alertmanager と vmalert の画面は port-forward で見ます。

```bash
kubectl -n observability port-forward svc/alertmanager 9093:9093    # http://localhost:9093/
kubectl -n observability port-forward svc/vmalert 8880:8880          # http://localhost:8880/vmalert/
```

## メトリクス

[victoria-metrics.yaml](../manifests/platform/observability/victoria-metrics.yaml) の scrape 設定が正本です。

| job | 対象 |
|---|---|
| `kube-state-metrics` | Kubernetes のオブジェクトの状態 |
| `kubelet-volume-stats`、`kubelet-resources` | 各ノードの kubelet（10250）。PVC の容量、Pod の CPU とメモリ |
| `kubelet-node-filesystem` | 各ノードのルートディスクの容量と使用量だけ（60 秒間隔） |
| `host-node` | ホストの node_exporter（`192.168.122.1:9100`） |
| `kubernetes-pods-annotated` | `prometheus.io/scrape` の annotation を付けた Pod |

- kubelet には ServiceAccount の token で接続し、クラスタ CA で証明書を検証します（[serving 証明書](nodes.md#kubelet-の-serving-証明書) が前提）。
  権限は `nodes/metrics` の `get` だけで、`nodes/proxy` は与えていません。
- アプリの metrics を集めるには、Pod に annotation を付け、VictoriaMetrics からの受信を [NetworkPolicy](network-policy.md#許可を足す) で許可します。
- CPU の throttling は収集していません。

## ログ

Alloy が Kubernetes の API 経由で **`sandbox` の Pod のログだけ** を集め、Loki に送ります。保持は 7 日です。

```logql
{namespace="sandbox", container="api-hub"}
```

- 基盤の Pod（ArgoCD、Harbor、監視、kube-system など）のログは `kubectl logs` で見ます。
- ノードの journald、API サーバーの監査ログは集めていません。
- 「02 アプリ・ログ」は、[workload-rules.yaml](../manifests/platform/observability/config/workload-rules.yaml) が記録する
  「Pod がどのアプリに属するか」を使ってログをまとめます。CronJob は CronJob 名、ほかは Pod の `app.kubernetes.io/name` → `app` → 所有者名の順です。
  消えた Pod のログも、保持期間内なら選べます。

## 外形監視

Alloy が 1 分ごとに、クラスタ内から次を確認します（[config.alloy](../manifests/platform/observability/config/config.alloy) の `target`）。

- ArgoCD、Harbor、Grafana、blog、cooklog、api-hub、rsshub、dashboard、hitomi-upload-viewer の HTTPS 応答（CoreDNS → Gateway → TLS → アプリ）
- Split DNS（`192.168.122.101`）の名前解決
- API サーバーの `/livez`（証明書の期限の取得を兼ねる）

クラスタの中からの確認なので、LAN や tailnet の端末から届くか（MetalLB の広告、subnet router）は分かりません。

## アラート

式と継続時間は [alert-rules.yaml](../manifests/platform/observability/config/alert-rules.yaml) が正本です。
ルールには [promtool のテスト](../automation/tests/observability/) があり、変えたらテストも直します。

| 分類 | アラート | 条件の概要 |
|---|---|---|
| クラスタ | `KubernetesNodeNotReady`、`KubernetesPodNotReady`、`KubernetesPodRestarting`、`KubernetesDeploymentUnavailable` | NotReady、15 分間に 3 回以上の再起動、replica 不足 |
| | `KubernetesCronJobFailed` | 直近 6 時間に始まった Job が失敗 |
| | `KubernetesPVCNotBound`、`KubernetesVolumeSpaceLow` / `Critical` | PVC が Pending、空き 20% / 10% 未満 |
| | `KubernetesNodeDiskSpaceLow` / `Critical`、`KubernetesNodeDiskMetricsUnavailable` | ノードのルートディスクの空き、計測の欠損 |
| GitOps・基盤 | `ArgoCDApplicationOutOfSync`、`ArgoCDApplicationUnhealthy` | 15 分 / 10 分続く |
| | `ExternalSecretNotReady`、`GatewayNotProgrammed`、`CertificateNotReady` | Ready でない |
| | `NetworkPolicyControllerUnavailable`、`NetworkPolicyControllerMetricsUnavailable` | controller の停止 |
| 証明書 | `EndpointCertificateExpiring`（14 日未満）、`ControlPlaneCertificateExpiring`（45 日未満）、`KubeletServingCSRPending` | [対処](nodes.md#kubelet-の-serving-証明書) |
| 外形 | `EndpointProbeFailed`、`EndpointProbeMissing` | 応答や名前解決の失敗が 5 分、結果の欠損が 15 分 |
| ホスト | `HostMetricsUnavailable`、`HostMemoryLow`、`HostSwapThrashing`、`HostCPUPressure`、`HostDiskSpaceLow` | [容量の考え方](nodes.md#容量の考え方) |
| | `HostStorageUnavailable`、`HostStorageReadOnly`（critical）、`HostStorageSpaceLow` | NFS の実体の HDD。[HDD](nodes.md#hdd)、[戻し方](troubleshooting.md#hdd-が外れた) |
| | `HostRebootRequired`、`HostSecurityUpdatesPending` | 7 日続く |
| データ | `PostgreSQLBackupStale` | dump が 26 時間成功していない |
| | `RustFSScannerStalled`、`RustFSScannerMonitorMissing` | [scanner](storage.md#scanner) |
| 監視自体 | `KubeStateMetricsUnavailable`、`KubeletVolumeScrapeUnavailable` | 収集の失敗 |
| アプリ | `ApplicationQueue*`、`ApplicationUnreviewedFailure`、`Camera*` | 下記 |

監視していないもの: RAID の中の 2 台それぞれの状態、LAN / tailnet からの到達、HTTPRoute ごとの状態、バックアップが実際に復元できるか。

## 通知

- **Slack**: Alertmanager が発火と復旧の両方を送ります。集約の待ちは 30 秒、未復旧の再通知は 4 時間ごとです
  （[alertmanager.yaml](../manifests/platform/observability/config/alertmanager.yaml)）。Webhook は Secret `alertmanager-slack` です。
- **デッドマンスイッチ**: 常に発火している `Watchdog` を、Alertmanager が 1 分ごとに healthchecks.io へ送ります（Secret `alertmanager-heartbeat`）。
  クラスタ、ホスト、回線、vmalert、Alertmanager のどれが止まっても送信が途絶え、healthchecks.io がクラスタの外から通知します。
  healthchecks.io 側の設定（Period 5 分 / Grace 5 分、通知先）は Git にありません。
- **週次レポート**: CronJob [weekly-observability-report](../manifests/platform/observability/weekly-report.yaml) が月曜 09:00 JST に直近 7 日の状態を Slack へ送ります。
  送らずに本文を見るには次を実行します。CronJob から手で Job を作ると、実際に Slack へ送られます。

  ```bash
  kubectl -n observability port-forward svc/victoria-metrics 18428:8428 &
  VICTORIA_METRICS_URL=http://localhost:18428 \
    python3 -B manifests/platform/observability/scripts/weekly-report.py --dry-run
  ```

ArgoCD の同期の成功・失敗は、ArgoCD が別に通知します（[GitOps](gitops.md#通知)）。

## アプリのキュー

Pod が Ready でも、仕事が進んでいるとは限りません。アプリは自分のキューの状態を metrics で出し、それを監視しています。

| queue ラベル | 出どころ | 内容 |
|---|---|---|
| `notifications` | api-hub:3000 | Slack への配送 |
| `hitomi_outbox` | hitomi-outbox:9090 | api-hub への受け渡し |
| `hitomi_items` | hitomi-outbox:9090 | 収集した項目の処理 |
| `media` | media-api:3000 | メディアの取得 |

主な metrics:

- `application_queue_jobs{queue,state}`: 状態ごとの件数
- `application_queue_oldest_pending_age_seconds{queue}`: いちばん古い未完了の経過時間
- `application_queue_last_success_timestamp_seconds{queue}`: 最後に成功した時刻
- `application_failure_records{queue,disposition}`: 失敗の件数（`unreviewed` / `acknowledged` / `excluded`）

```promql
sum by (queue, state) (max by (queue, state) (application_queue_jobs{namespace="sandbox"}))
```

| アラート | 条件 | 最初に見るところ |
|---|---|---|
| `ApplicationQueueFailed` | 通知に failed / uncertain がある | Slack に届いているか確認してから手で再試行する。一括で再送しない |
| `ApplicationUnreviewedFailure` | 取得や収集に、未確認の失敗がある。`hitomi_sources` は次の収集でも失敗して 4 時間30分続いたときだけ | hitomi の管理 CLI、または viewer の「失敗の管理」で原因を見て、確認済みにするか対象外にする |
| `ApplicationQueueBacklog` | 通知と outbox は 15 分、media と収集は 2 時間たっても未完了 | worker のログ、DB、S3、取得元。減っているのか止まっているのかを見る |
| `ApplicationQueueMetricsUnavailable` | metrics を取れない | Pod、NetworkPolicy、DB 接続。DB 障害のときに worker を再起動し続けない |
| `CameraRecordingStalled`、`CameraNotRecording` | 録画する時間帯なのにファイルが確定しない | recorder のログ、カメラへの接続 |
| `CameraSpoolBlocked`、`CameraUploadBacklog` | spool が上限、未送信が溜まっている | uploader のログ、api-hub、S3 |
| `CameraPartialSegment`、`CameraInvalidManifest` | 書きかけ、壊れたファイルがある | [home-camera](apps.md#home-camera) の隔離手順 |
| `CameraSpoolStatisticsUnavailable` | spool の集計に失敗 | recorder のログ、NFS |

キューが空の時間帯は、最後の成功が古くても通知しません。「確認済み」にした失敗は通知が止まりますが、自動の再試行は続きます。

各 worker は、キューが空のときに確認の間隔を 1 秒から 30 秒まで延ばします。新しい仕事が処理され始めるまで、最大 30 秒ほど待つことがあります。

## 動かないとき

```bash
kubectl -n observability get pods,pvc,externalsecrets,cronjobs
kubectl -n observability logs deployment/vmalert --tail=50
kubectl -n observability logs deployment/alertmanager --tail=50
kubectl -n observability logs deployment/alloy --tail=50
```

VMUI の `/targets` で、収集に失敗している対象とエラーを見られます。
