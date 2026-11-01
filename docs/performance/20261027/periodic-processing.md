# 定期処理の間引き・本番反映確認

2026-10-27 JST。資格情報の定期反映を30分間隔へ変更し、api-hub / hitomi outbox /
media worker の空キュー・DB障害時の待機を最大30秒にした。
通常処理後は1秒へ戻す。キューの内容・再試行日時・lease・migration は変更していない。

## ソース・公開イメージ

| 対象 | ソース commit | 公開 CI | 本番で照合した image digest |
|---|---|---|---|
| api-hub | `ecbb21b` | [40430969509](https://github.com/ksera524/api-hub/actions/runs/40430969509) | `sha256:ca619f4908f60cb2ab00e9c801d792171d22a4db1f7bbe11412a0e6e438f0a95` |
| hitomi | `ff4ee90` | [40430971825](https://github.com/ksera524/hitomi/actions/runs/40430971825) | `sha256:e5bc4968263a5e26a63a26e450a4974933c1c55ec21fef66f146ce61c5e97b2d` |
| media | `567e95d` | [40430973776](https://github.com/ksera524/hitomi-pdf-batch/actions/runs/40430973776) | `sha256:0949316b9925aba773580066b72c59818513dca54547c03293d1e0a1e3e98fd4` |

3件とも対象ソースのビルド・復旧テスト・公開が成功。
media は既存viewerのブラウザ検証も成功し、viewer image 自体の変更・再公開はない。
GitOps の設定 commit は `2fd64e8`、その[公開CI](https://github.com/home-lab-ksera/home-lab-k8s/actions/runs/40430976136)も全成功。
Image Updater の Git write-back は api-hub `6199ada`、hitomi `2daa769`、media `6b685c5`。
3つの自動更新 commit の CI もすべて成功した。

ArgoCD の Healthy 表示だけで更新完了と判断せず、実 Pod の imageID と
公開jobの `crane push` が出力した manifest digest を照合した。
管理端末からの Application hard refresh を利用し、workload の直接変更はしていない。

## キュー確認間隔の実測

DB の `pg_stat_activity` を約1秒間隔で読み、3種類のclaim SQLの `query_start` の変化を記録した。
SQL本文・通知本文・保存キーは記録せず、queryの種類と時刻で識別した。
反映後は接続元Pod IPも照合。履歴から直近queryを観測するため、すべてのSQL実行を数える測定ではない。

| worker | 反映前の間隔中央値 | 反映後の間隔中央値 | 反映後の観測区間数 |
|---|---:|---:|---:|
| api-hub 通知 | 1.010秒 | 30.010秒 | 4 |
| hitomi outbox | 1.005秒 | 30.008秒 | 4 |
| media | 1.012秒 | 30.010秒 | 3 |

- 反映前: 10:11:17〜10:11:53、35回の読み取り。
- 反映後: 10:24:32〜10:27:40、180回の読み取り。起動直後のbackoffを除き、10:25:25以降のclaim時刻を集計。
- 読み取り失敗は前後とも0件。反映後の観測間隔は約30.005〜30.013秒。
- 少数標本の空キュー時の測定。CPU使用量・総SQL数の削減率や、新着の処理速度を実測した結果ではない。

集計値は [queue-polling-summary.json](queue-polling-summary.json)。
通常処理後に1秒へ戻ること、待機の上限、既存の冪等性・終了処理・復旧は各アプリの回帰テストで確認した。
本番へ試験通知・取得Jobを投入しておらず、新構成での新たな実配送成功を主張しない。
2段階の通知経路では待機が合計約60秒になる場合がある。

## 稼働と業務状態

10:26台の確認で対象4 PodすべてReady、再起動0、新Podのログにエラー・panicなし。10:30台にも継続稼働を確認。
api-hub / hitomi / hitomi-pdf / shared-postgresql は Synced / Healthy、同期操作は Succeeded。

- api-hub: 外部HTTPS経路の `/health` が正常。port-forward の `/health` / `/metrics` もHTTP200。
- media API: port-forward の `/health` / `/ready` / `/metrics` がHTTP200。
- hitomi outbox: port-forward の `/metrics` がHTTP200。
- 既存VictoriaMetricsから新Podの3つのmetrics endpointのscrape成功（`up=1`）を確認。

API Server の Service / Pod proxy 経路ではtimeoutが発生したため、上記の外部HTTPS・
port-forward・既存scrapeで確認した。proxy経路の原因はこの作業では特定・変更していない。

変更前後とも、通知はsent52 / pending・retry・uncertain 0、outboxはaccepted52 / pending・verify 0、
mediaはdone305 / failed1、収集itemsはretry10だった。既存media失敗の警告は継続しており、
今回の配備で失敗行を消去・一括再試行したり、成功へ書き換えたりしていない。

## 資格情報Job

実CronJobのscheduleが `*/30 * * * *`、Forbid維持であることを確認。
通常同期の `postgresql-credentials-sync` PostSync Job は `2fd64e8` の同期操作で Succeeded。
成功後のHook削除は `HookSucceeded` の仕様どおり。
新設定反映後、旧2分間隔のJobは作成されていない。
10:30:00 JST の定期Jobが予定どおり起動し、10:30:07に成功したことも確認した。

ローテーション時の即時実行と失敗時の確認は[共通PostgreSQLの運用](../../shared-postgresql.md#資格情報の反映)を参照。
