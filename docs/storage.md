# ストレージ

## StorageClass

| StorageClass | 実体 | 削除時 | 使い道 |
|---|---|---|---|
| `local-path` | ノードのディスクの `/mnt/k8s-storage`（VM の中） | 実データも消える | 速さが要るもの、消えても作り直せるもの |
| `nfs-external` | ホストの HDD（NFS） | `archived-` を付けたディレクトリとして残る | 容量が要るもの、VM を作り直しても残したいもの |
| `nfs-postgresql` | 同じ NFS | PV ごと残る（Retain） | PostgreSQL の dump |

local-path の PV は、最初に Pod が動いたノードに固定されます。現在はすべて `k8s-worker2` にあります。
PVC の要求サイズは上限ではありません。local-path も NFS も、実際のディスクが埋まるまで書けます。

## PVC

| Namespace / PVC | StorageClass | サイズ | 中身 |
|---|---|---|---|
| database / `data-postgresql-0` | local-path | 10Gi | PostgreSQL のデータ |
| database / `postgresql-backup` | nfs-postgresql | 5Gi | 日次の dump（7 日分） |
| observability / `loki-data` | local-path | 20Gi | ログ（7 日） |
| observability / `grafana-data` | local-path | 2Gi | Grafana の内部 DB |
| observability / `alloy-data` | local-path | 1Gi | 送信待ちのログ |
| observability / `victoria-metrics-data` | nfs-external | 20Gi | メトリクス（30 日） |
| harbor / `harbor-registry` | nfs-external | 10Gi | コンテナイメージ |
| harbor / `harbor-jobservice`、`data-harbor-redis-0` | nfs-external | 各 1Gi | ジョブのログ、Redis |
| rustfs / `rustfs-data` | nfs-external | 3Ti | オブジェクト |
| rustfs / `rustfs-logs` | nfs-external | 10Gi | RustFS のログ |
| sandbox / `home-camera-spool` | nfs-external | 100Gi | 送信前の録画 |
| sandbox / `nature-remo-data` | nfs-external | 1Gi | 登録したリモコン信号 |

## データの保護

別の媒体へのバックアップはありません。守り方はデータごとに次のとおりです。

| データ | 保護 | 失ったとき |
|---|---|---|
| PostgreSQL | 日次の dump を NFS（HDD RAID1）へ | 最大 1 日分を失う（[復元](postgresql.md#復元)） |
| RustFS のオブジェクト | HDD RAID1 のみ | PDF と動画は取得し直せる。録画は戻らない（30 日で消えるデータ） |
| コンテナイメージ | HDD RAID1 のみ | 各アプリの CI を再実行する |
| メトリクス、ログ、Grafana の内部 DB | なし | 過去分を失う。ダッシュボードとデータソースは Git にある |
| リモコン信号（nature-remo） | HDD RAID1 のみ | 登録し直す |

ホストの HDD が両方壊れた場合と、誤って消した場合は戻せません。RAID の片側の故障は、ケースの LED とブザーでしか分かりません（[HDD](nodes.md#hdd)）。

## NFS のディレクトリ

ホストの `/mnt/k8s-storage/nfs-share/` に、PVC ごとに `<namespace>-<pvc>-<pv>` のディレクトリができます。

- PVC を消すと、`nfs-external` のディレクトリは `archived-<元の名前>` に改名されて残ります。不要なら手で消します（root 権限が要ります）。
- クラスタを作り直すと PV の名前が変わり、新しい空のディレクトリができます。古いディレクトリの中身は自動では引き継がれません。
  引き継ぐときは、Pod を止めてから中身を新しいディレクトリへ移します。
- NFS の `kubelet_volume_stats` は HDD 全体の値です。PVC ごとの使用量ではなく、足し合わせることもできません。

容量の確認:

```bash
kubectl get pv,pvc -A
ssh ksera@192.168.10.11 'df -h /mnt/k8s-storage; du -sh /mnt/k8s-storage/nfs-share/* 2>/dev/null | sort -h | tail'
```

## RustFS

単一 replica の S3 互換ストレージです。chart を [Kustomize で包んで](../manifests/platform/rustfs/kustomization.yaml) います。

| 項目 | 値 |
|---|---|
| S3 | クラスタ内 `rustfs-svc.rustfs.svc:9000`。外からは `s3.internal.qroksera.com` の読み取りだけ |
| コンソール | `https://rustfs.internal.qroksera.com` |
| bucket | `pdfs`（PDF、プレビュー、動画）、`home-camera-recordings`（録画） |
| 認証 | Secret `rustfs-auth`（rustfs と sandbox に同じもの） |
| 接続元 | S3 に直接つなぐのは api-hub と lifecycle Job だけ。ほかのアプリは api-hub 経由 |

### 30 日で消す

両方の bucket に「30 日で期限切れ」の lifecycle を設定しています。
同期のたびに PostSync Job `rustfs-lifecycle` が [lifecycle.json](../manifests/platform/rustfs/lifecycle.json) と
[cors.json](../manifests/platform/rustfs/cors.json) を [buckets.txt](../manifests/platform/rustfs/buckets.txt) の bucket に適用します。
bucket 自体は作りません。日数や対象の変え方は [rustfs/README.md](../manifests/platform/rustfs/README.md) を参照してください。

```bash
kubectl -n rustfs logs job/rustfs-lifecycle
```

### scanner

実際に消すのは RustFS の scanner です。lifecycle の設定が入っていても、scanner が止まっていると何も消えません。
CronJob [rustfs-scanner-monitor](../manifests/platform/rustfs/scanner-monitor.yaml) が 15 分ごとに scanner の状態を読み、VictoriaMetrics に送ります。

- `RustFSScannerStalled`: 走査が 6 時間完了していない（通常は 10 分ほどで 1 周します）。
- `RustFSScannerMonitorMissing`: 監視の CronJob 自体が値を送れていない。

`RustFSScannerStalled` が出たら状態を見ます。

```bash
kubectl -n rustfs port-forward svc/rustfs-svc 19000:9000 &
AK=$(kubectl -n rustfs get secret rustfs-auth -o jsonpath='{.data.RUSTFS_ACCESS_KEY}' | base64 -d)
SK=$(kubectl -n rustfs get secret rustfs-auth -o jsonpath='{.data.RUSTFS_SECRET_KEY}' | base64 -d)
curl -s --aws-sigv4 "aws:amz:us-east-1:s3" --user "$AK:$SK" \
  localhost:19000/rustfs/admin/v3/scanner/status | jq '.freshness, .cycle_recovery'
```

`cycle_recovery.classification` が `usage_floor_load_failed` なら、使用量の状態を作り直します。
オブジェクトは消えません。走査が再開すると、期限切れのオブジェクトがまとめて消えます。

```bash
curl -s --aws-sigv4 "aws:amz:us-east-1:s3" --user "$AK:$SK" -X POST -H 'Content-Type: application/json' \
  -d '{"mode":"full-rebuild"}' localhost:19000/rustfs/admin/v3/scanner/usage-state/reset
```

RustFS は更新時に必ず停止します（replica 1、`maxUnavailable: 1`）。その間、api-hub の S3 操作は失敗して再試行されます。

## Harbor

自作イメージのレジストリです。DB は [共通の PostgreSQL](postgresql.md) の `registry`、イメージの実体は NFS の `harbor-registry` です。
脆弱性スキャン（Trivy）は無効で、スキャンは各アプリの CI が push の前に行います。

次の設定は Harbor の DB にあり、Git にはありません。Harbor を作り直したら UI で設定し直します。

| 項目 | 値 |
|---|---|
| project | `sandbox`、quota 4GiB。超えると push が失敗する |
| 保持ルール | 全 repository で「直近に push された 5 件を残す」。毎日 03:00 JST |
| GC | 毎日 03:30 JST。タグなしの artifact は消さない設定 |
| Robot Account | CI 用（`sandbox` の push / pull）、Image Updater 用（pull のみ） |

保持と GC の履歴は、UI の Projects → sandbox → Policy と、Administration → Clean Up → History で見られます。
稼働中のイメージは、その repository の直近 5 件に入っている限り消えません。

Harbor が止まっている間も、ノードにイメージが残っていれば Pod は起動します。新しいノードや、イメージが GC されたノードでは起動できません。
