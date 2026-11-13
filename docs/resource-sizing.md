# RustFS / Harbor の resources

個人 home-server の単一ホスト・worker 2 VM を前提に、ストレージとレジストリの
CPU / memory requests、memory limits を設定する。予約量をスケジューラへ伝え、
実測に余裕を加えてメモリを制限する。requests の削減量は実メモリの削減量ではない。

## 設定の根拠

2026-10-27 07:22 JST、VictoriaMetrics の保存済みメトリクスを読み取りで確認した。
7 日の範囲に存在する各コンテナ系列の最大値であり、同時使用量や負荷試験の結果ではない。
memory は working set、CPU は 2 分平均の最大で、収集間隔より短いピークは含まない。
旧 Pod の系列も集計対象。RustFS の過去の再起動理由は Unknown で、OOM と断定しない。

| コンテナ | memory 最大 (MiB) | CPU 最大 (core) | CPU request / limit | memory request / limit |
|---|---:|---:|---|---|
| RustFS | 1605.4 | 0.936 | 100m / なし | 1536Mi / 3Gi |
| Harbor core | 101.5 | 0.035 | 50m / 200m | 128Mi / 256Mi |
| Harbor portal | 12.6 | 0.003 | 25m / 100m | 32Mi / 128Mi |
| Harbor jobservice | 17.8 | 0.013 | 50m / 150m | 64Mi / 256Mi |
| Harbor registry | 72.4 | 0.112 | 50m / 500m | 128Mi / 256Mi |
| Harbor registryctl | 34.1 | 0.003 | 25m / 100m | 64Mi / 128Mi |
| Harbor nginx | 16.5 | 0.001 | 25m / 200m | 32Mi / 128Mi |
| Harbor Redis | 10.4 | 0.014 | 25m / 250m | 64Mi / 256Mi |

RustFS の memory p95 は約 1210 MiB。request は 1536Mi、limit は観測最大の約 1.9 倍とする。
CPU は request を設定し、転送の瞬間的な負荷を制限しない。
Harbor registry の CPU limit は、従来の未適用値 200m より余裕のある 500m とする。
Harbor core は既存設定を維持し、portal / jobservice の memory request だけを減らす。
Harbor の Trivy は2026-11-11に無効化した（[全体最適化計画 6.1](../tasks/capacity-optimization-plan.md#61-harbor-trivy-の無効化)）。

Harbor 1.19.4 は `registry.registry.resources`、`registry.controller.resources`、
`redis.internal.resources` を参照する。旧 `registry.resources` と `chartmuseum.resources` は
この chart の稼働コンテナへ反映されないため削除した。nginx の設定も追加する。
`automation/tests/ci/test_platform_resources.py` が実際の Kustomize / Helm 展開結果で
全常駐コンテナの CPU / memory requests と memory limits の存在を検証する。

対象の常駐コンテナの memory requests 合計は、旧実設定 576Mi から 2304Mi になる。
RustFS の未計上分を含むため予約量は増えるが、これだけで同量の実メモリを追加消費するわけではない。
VM のリソースや replica 数は変更しない。

## 再計測

既存の [VMUI / Grafana](observability.md) で次を評価する。

```promql
max by (namespace, container) (
  max_over_time(container_memory_working_set_bytes{
    job="kubelet-resources", namespace=~"rustfs|harbor"
  }[7d])
) / 1024 / 1024
```

```promql
max by (namespace, container) (
  max_over_time(rate(container_cpu_usage_seconds_total{
    job="kubelet-resources", namespace=~"rustfs|harbor"
  }[2m])[7d:2m])
)
```

既存の収集対象には CPU throttling の指標を含まない。
CPU の平均値だけで制限の影響がないとは判定せず、転送時間も確認する。
memory working set はコンテナの全メモリ課金量と同一ではないため、limit 到達も別途確認する。
Kubernetes の動作は [公式の resources 説明](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/) を参照。

## GitOps 反映後の確認

この設定は Git 経由の ArgoCD 同期で反映する。ローカルの修正・検証完了と本番反映を区別する。
RustFS は単一 replica で `maxSurge: 0` / `maxUnavailable: 1` のため、更新中に停止が発生する。
Harbor registry は rolling update で 2 コンテナ分の request が一時追加される。
PVC の変更はなく、同期前後で既存 PVC が保持されていることを確認する。

```bash
kubectl -n argocd get applications rustfs harbor
kubectl -n rustfs get pods,pvc
kubectl -n harbor get pods,pvc
kubectl -n rustfs get deployment rustfs -o jsonpath='{.spec.template.spec.containers[*].resources}'
kubectl -n harbor get deployment harbor-registry -o jsonpath='{.spec.template.spec.containers[*].resources}'
```

同期後は新 Pod が Ready であること、Pending / OOMKilled / 新たな再起動がないことを確認する。
通常の録画アップロード、大きめのメディア転送、イメージ push/pull を含む期間で
再計測し、待機時の小さい値だけを見て上限を下げない。
問題があれば Git で今回の resources 変更を戻して ArgoCD 同期する。

## 未設定アプリの初期予算（2026-10-28）

2026-10-28 10:59 JST 頃、VictoriaMetrics の直近7日を読み取りで確認した。
上記と同じ working set 最大・CPU 2分平均最大で、旧 Pod 系列も含む。
通常時・処理時を分離した負荷試験ではなく、短いピークや未経験の負荷は保証しない。

| コンテナ | memory 最大 (MiB) | CPU 最大 (core) | CPU request | memory request / limit | ephemeral-storage request / limit |
|---|---:|---:|---|---|---|
| blog | 11.2 | 0.000546 | 10m | 32Mi / 128Mi | 128Mi / 512Mi |
| cooklog | 11.2 | 0.000389 | 10m | 32Mi / 128Mi | 128Mi / 512Mi |
| hitomi-upload-viewer | 1.2 | 0.000218 | 10m | 16Mi / 128Mi | 128Mi / 512Mi |
| hitomi-monitor 本体 | 159.3 | 0.0581 | 100m | 256Mi / 1Gi | 1Gi / 4Gi |

hitomi-monitor の memory limit は2026-11-13に 1Gi へ上げた。AVIF 画像の変換で一時的に約 520MiB に達し、512Mi で cgroup OOM（Pod 表示は exit 137 / Error）となって同じ作品で毎回停止していた。30秒間隔の working set には現れない短いピーク。

上記には CPU limit を設けず、空き CPU で処理を進められるようにする。
RustFS・Harbor・既存 Selenium の設定は維持する。

一時領域は現在の監視で未計測のため暫定値。
hitomi のディスク上の `/tmp` emptyDir は 3Gi に制限する。
ノードのイメージキャッシュはこの Pod limit で直接制限できず、Selenium のメモリ型 `/dev/shm` はメモリ予算となる。

常駐3アプリの requests 合計は CPU 30m、memory 80Mi、ephemeral-storage 384Mi。

GitOps 同期後に常駐3アプリの Ready と実 resources、CronJob の次回 Pod の resources を確認する。検証目的の業務 Job / workflow は起動しない。
通常アクセスと大きな hitomi 処理を含む期間で再計測し、
OOM / eviction / Pending、応答時間・完了時間を確認して見直す。
