# 簡素化の実行記録

2026-10-25 JST。基準HEAD: api-hub `14f3e5b`、hitomi `5b5949c`、media `8ee1e4d`、camera `e9fffee`、GitOps `f2421ec`。全対象の作業treeは開始時clean。

## P0: 切替前

19:20〜19:23 JSTにread-onlyで確認。ArgoCDの全ApplicationはSynced/Healthy。

| 対象 | 実Podのimage digest | 実処理の状態 |
|---|---|---|
| api-hub API/worker | `sha256:da6986ab1aab6903feca8d49f19edcd2a4d9e8cd6e60921824578032538d9871` | notificationsは0件。配送成功の証拠とはしない |
| hitomi outbox | `sha256:afa8a3b592cc64fc26a623a39d54edfe3d3e724cea6150cb9a908169da52c005` | sources/items/notification_outboxは0件、初回baseline前 |
| media API/worker | `sha256:b5de462ed830998a79d4dedb95b82df1adb2868d64c569278474b888a06572c2` | PDF/preview各ready 1459・expired 50、video ready 336・failed 3 |
| camera recorder/uploader | `sha256:1ecc1586e2a56fac3c8efac8786ff88f63597ed9927c678eb94ce1a4fd3bfdb0` | recordings 0件、spoolはlock/空sessionだけ。DBだけに残る未完了行なし |

cameraは録画時間帯にもかかわらず、spool bytes 0、recording_active 0、recorder_restarts_total 122。実録画は未成功。既知のRTSP引数不備の修正と人工RTSP試験をP2で扱う。時間帯外の正常待機とは分類しない。
hitomiは22:55 JSTの日次schedule。既存のsources/items/outboxを初期化せず継承する。
通知本文・資格情報は取得記録へ含めていない。DBの確認は件数・状態のSELECTのみでfixtureを投入していない。

## P1

通常media manifestと旧切替exampleからmedia-import hookを除去。生成器もhookを生成しない。
明示Jobと対象・digest・理由を指定する[手順](../automation/examples/media-import/README.md)を追加。
過去の切替記録のPostSync成功は履歴として保持する。

以降の変更・検証結果は段階ごとに追記する。配備していない変更を配備済みとは記録しない。

検証: `nix develop --command automation/scripts/ci/validate.sh`成功。mediaの`cargo test --locked`は24件成功、隔離PostgreSQLで`durable_media_recovery`成功（import 2回の冪等性と取得Job非生成を含む）。

## P2: camera

完成mp4と既存キーのmanifestを正本にし、uploader.lockの排他で再送。SQL claim/lease/heartbeat、sqlx依存、CIのPostgreSQLを撤去。旧manifestは追加フィールド省略を許容。失敗回数と次回試行をatomic renameで保存し、1件失敗しても走査を継続する。

S3 HEADは404だけPUTへ進み、認証・一時障害・サイズ不一致を失敗として保持。ローカルchecksum確認を維持し、S3キー/サイズの照合後に動画→manifestの順で削除する。動画だけ消失したmanifestはS3を照合し、両方欠損は保持する。破損manifestは再生成せず保持し、専用件数とアラートで検知する。

RTSPは`-timeout`で接続を制限し、完成segmentがsegment長の2倍+起動猶予60秒停止すると再接続する。DBなし回復試験は7件成功（FFmpeg 7.1.1、実segment継続、RTSP無応答と再接続を含む）。

配備順序: 先に互換imageの回復試験を通す。旧設定の`--record`/`--upload`で起動でき、旧migration Job向け`--migrate`はDBを変更せず終了する暫定互換（P6で削除）。その後GitOpsからPG設定/CA/DBラベル/migrationを除去し、StatefulSetの順序付き更新で旧uploader終了後に新uploaderを起動する。強制削除しない。PVC/S3の移動・削除を行わない。


## 2026-10-26 再開時の確認

P1: media #3、GitOps #117をマージし配備済み。ArgoCDのhitomi-pdf同期対象にimport Jobなし。P2アプリhome-camera #7をマージ、公開CI成功、新digest `sha256:106847f78175cc4e478c471ba71704eefe284604a30b228b0faf1e3c51ce101c`が配備済み。07:24 JST、S3の2026/10/25 UTC prefixに146 object/1,671,012,127 bytes。spoolは現在のpart/active/CSV/locksだけで、完成分は解放済み。recording_active=1、invalid_manifests=0、stalled=0、partial=0、stats_success=1。GitOps #118も全CI成功後マージ。PG設定除去の反映と09:00 JSTの時間帯終了確認は追跡する。

hitomiの日次Jobはsources 11（initialized）、baseline 852件を保存し、outboxとapi-hub通知は0件。123hon 1件とcomic-walker 2件のRSS取得timeoutにより3 failed operationsで終了。初回全経路の成功とはしない。既存履歴を維持して次の収集へ引き継ぐ。

## P3

通知受付をoutbox accepted/items notification_acceptedで終端にする。追加migration 2で既存行を分類し、api_id付き非sent行をverifyで一度照合。不一致等はreceipt_failedとして保持。有効な202まで同じpayloadを再送し、配送結果の継続GETは撤去する。

同一feed6回・章revision更新・再掲載・baseline維持、応答喪失/503/429/ID欠落202からの同一payload再送、400/409、移行照合のidentity不一致、受付後の非GETを隔離DBで確認。回復試験3件成功。通常lib試験58件成功。

先にアプリimage/migrationを配備し、api-hubの配送失敗監視が有効な状態で受付責任を切り替える。その後、GitOpsのscheduleをJST 02:55/06:55/10:55/14:55/18:55/22:55に変更する。

P3の実運用確認で3件のRSS timeoutは、内部RSSHub Gatewayへのegressがmediaだけに許可され、hitomi-monitorに欠けていることを特定。既存のGateway/443の許可を両collectorへ適用し、他のLAN port・無関係なappは引き続き拒否する回帰試験を追加する。収集頻度変更に先立つ欠落防止の修正であり、source/items/outbox履歴は初期化しない。


## P2完了: 09:00 JSTの録画終了

2026-10-26 09:00 JSTにrecording_active=0、spool bytes=0、最古待機=0、partial=0、invalid_manifests=0、stalled=0、stats_success=1を確認。現Podのrecorder再起動0、spoolは2つのlockだけ。S3の2026/10/25 UTC prefixは167 object / 1,807,333,500 bytes（一覧は非truncated）。連続segmentの確定・保存・解放から時間帯外停止までを確認した。開始時の旧RTSP不備とStatefulSet交代の中断があるため、14時間無欠落とは記録しない。

cameraのPG環境変数・CA・DBラベル・migrationはGitOps #118で撤去済み。退役camera DBは存在しrecordings 0件、過去migration履歴を保持。PGOのDB宣言からの除外は物理DROPを行わないことを仕様と実DBで確認した。

## P3配備・通常収集

hitomi #26 / GitOps #119を配備。digest `sha256:2cf7f6cb878c7d92e25ebcfa0b3da87b2c423dc32470ba4ec4866435feb47eec`、migration2を確認。RSSHub 3経路はHTTP200へ回復。通常CronJobから作成した `hitomi-simplification-20261026` は14source初期化、baseline861、notification_accepted9、filtered13、retry3。outbox accepted9に対しapi-hub sent9、pending/sending/uncertainは0だった。通知は通常収集で発生したものだけで、試験通知は送っていない。

Job自体は取得元の「No tags found for content」3件により失敗。3件はretryとして保持し、成功扱いや全件再取得をしない。画像decode警告5件は既存fallbackで処理できた。RSS取得障害とは分けて記録する。JST 1日6回/Forbidを維持し、source/items/outboxの履歴を初期化していない。

## P4: native HTTP

api-hub #12 / GitOps #120を配備。reqwest 0.12.28の共有Clientとaxum 0.8.9へ置換し、独自wire decoder/socket loop/TLS/接続poolを撤去。残るRequest/Responseは業務handlerのデータ型。S3署名と操作は維持し、redirect/暗黙retry/proxy/自動展開を無効化。署名targetをURL正規化が変える場合は送信しない（既存media keyに該当0件）。

API/stream22件、HTTP6件、隔離通知回復1件、static Nix/CPU/CA/非rootimageのビルド起動を検証。64/256MiBの実binaryのpeak RSSはupload22,016/22,144KiB、download5,292/5,476KiB。ファイル全体をbufferしない。配備digest `sha256:8b76b99ef8335728a8f3bc5d57fa927fec79de69fd50614c2e64fd3e08247894` で実PDF/動画のproxyと直接S3のRange 0-63のbyte一致、206/304/416、health/metrics200を確認。上記sent9もnative HTTPでの実配送だった。

GitOps #120は最後のCI完了前にmergeしてしまった。最終CIは全成功し、終了猶予90秒がHTTP新imageより先に反映されたことを確認した。以降は対象headのchecks成功を確認してからmergeする。

## P5: APIと配送taskの統合

api-hub #13の準備image `sha256:7293713df8a0d060217d122374951017e53aa38f555f6786fb05b43d7d504040` は既存API専用起動のまま配備し、GitOps #121で旧専用workerを0にした。Pod消滅とsending/uncertain 0を確認後、別PR #122でAPIを `--serve` / 1 replica / Recreate / 終了猶予90秒へ変更。09:09 JST起動の統合PodはReady、再起動0、health/metrics200。共有poolの2接続の一方に通知claim queryが残り、統合taskのDB走査を確認した。統合後は空キューのため、新たな実配送成功とは扱わない。

通常24件、HTTP7件、隔離DB回復3件が成功。実CLIにSIGTERMを送り、処理中Slack応答を待ってsentをcommitし、次のpendingをclaimせず終了すること、migrationは非配送であること、worker panic/異常終了の監督、DB障害中のHTTP200とmetrics503を確認。外部障害でPodを再起動しない。HTTP80秒/配送75秒をdrainし、task終了後にpoolを閉じる。CIのport probeはTIME_WAITをlistenerと誤認しないよう修正した。

## P6: 最終経路と運用

各PRの全checks成功後、camera #8、hitomi #27、media #4、api-hub #14をmergeした。cameraの引数なし旧録画/SQL互換migrate/旧hourlyコード、mediaの旧一括取得、hitomiのDBなし直接配送fallback、api-hubのAPI専用/worker専用起動を削除。api-hubは引数なし/`--serve`とも統合、`--migrate`はmigrationだけ。cameraは`--record`/`--upload`だけ、mediaの手動日付は`--plan YYYYMMDD`へ統一した。

最終app検証: cameraは通常とFFmpeg回復・不正引数、hitomiはlib60/HTTP3とDB必須、mediaは通常19/隔離durable recovery、APIは通常24/HTTP7/隔離3が成功。APIのmemory fixtureは配布imageに含まないtest専用exampleへ移し、製品へAPI専用flagを残していない。同じ2thread条件で64/256MiB debug fixtureのupload55,312/72,684KiB、download15,628/15,636KiBで既存閾値内。

GitOps #123で専用worker Deployment・旧camera CronJob・専用policy参照・未使用env・旧生成器を撤去した。[構成図](application-simplification-architecture.md)、[運用・復元手順](application-postgresql-cutover.md)、[接続予算](shared-postgresql.md)を最終形へ更新。DB復元時の重複通知は許容し、通常の統合起動で再開する。Slack事前照合用の起動modeは設けない。

対象常駐Pod6→5、container7→6、主処理pool上限18→14、全app最大見積り78→70（app80/cluster100）。09時台の待機時実測はapp接続11（api_hub2/hitomi2/media4/registry3、全idle）であり、ピーク実測とは扱わない。cameraは接続0。

清掃前の残課題は既存media video failed3件とhitomiのタグ欠落retry3件だった。最新件数は次節を参照。行を削除せず個別調査対象として保持する。これらを空キューや全件成功と記録しない。fixtureは隔離DBのみ、移行のための全件再取得・一括Slack再送・camera DB/PVC/S3削除を行っていない。

## 最終確認（2026-10-26 JST）

最終4アプリは同一commitの回復試験を通したartifactだけをHarborへ公開した。公開workflowとGitOpsのdigest、稼働PodのimageIDを照合した結果を以下に記録する。

| 対象 | 最終main commit | 公開workflow | 最終digest |
|---|---|---|---|
| api-hub | `3e5d521` | [40248246373](https://github.com/ksera524/api-hub/actions/runs/40248246373) | `sha256:dfc8dafb23f49ad43cbe4f2a100b6550c706f785921efe3278398de4d7ffbd46` |
| hitomi | `ceae70f` | [40248232023](https://github.com/ksera524/hitomi/actions/runs/40248232023) | `sha256:ddd21019e5d30862dd2203bdc78f66bd18f2b63447120ba9df1eef5d12cfd2b7` |
| media | `68ed7f4` | [40248240113](https://github.com/ksera524/hitomi-pdf-batch/actions/runs/40248240113) | `sha256:a196f9df2fb7537790206a0087db70e0529ba8d278e8c4dfb219fb70d7721b93` |
| camera | `ceef3aa` | [40248224355](https://github.com/ksera524/home-camera/actions/runs/40248224355) | `sha256:a9a6219b3ee4b08a46dd2d0a141e84bf25a7bc5d374b19a680461bcfbe94667c` |

09時台の最終照合で対象5Pod/6containerすべてReady・再起動0・公開digestと実imageID一致。api-hub/hitomi/hitomi-pdf/home-camera/core/shared-postgresql/observabilityはSynced/Healthy。専用通知worker Deploymentと旧camera CronJobは存在しない。実APIはserve/replica1/Recreate/pool3/grace90、hitomiは6回/Asia-Tokyo/Forbidを確認した。cameraは最終imageでもPG設定・DBラベルなし、spool bytes0/invalid0/stalled0/partial0/stats_success1、時間帯外停止、100GiBの既存PVCがBound。退役camera DBは存在しrecordings0件を再確認。録画時間帯の実処理証跡はP2段階のもので、最終清掃imageで一晩を再計測したとは扱わない。

最終APIで実PDF/動画のproxyと直接S3のRange64 byteが一致し、206/304/416、health/metrics200を再確認した。API共有poolは2接続、その一方にclaim queryを確認、通知はsent9。新しい試験通知は発生させていない。09:20の毎時reconcileは更新前imageで正常終了し、次回用Job templateは最終digestへ更新済み。P6でreconcile本体は変更していない。

定期取得が継続したため、mediaの最新状態はPDF/preview各ready1459/expired100、video ready368/failed4/pending1、ingest_jobs done173/failed4/retry1。失敗4件は容量上限・MP4 signature不一致・取得元429・403が各1、retry1件は取得元503。開始時のfailed3件だけを最新値として扱わない。hitomiはbaseline861/filtered13/notification_accepted9/retry3、outbox accepted9。取得失敗は個別に残し、一括retryや成功状態への書換えを行っていない。

清掃のGitOps PRは[#123](https://github.com/home-lab-ksera/home-lab-k8s/pull/123)、最終配備確認は本節に記録。通常のmedia importは実行しておらず、手動Jobの見本だけを上記検証済みmedia digestへ更新した。
