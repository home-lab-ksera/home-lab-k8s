# アプリケーションの運用・切替・復元

現行構成は[簡素化後のアプリ構成](application-simplification-architecture.md)。2026-10-24〜25のPostgreSQL初回切替時の専用通知worker・camera DB依存・PostSync importを通常運用へ戻さない。

## 通常の起動

| 対象 | 起動・責任 |
|---|---|
| api-hub | 1 replica/Recreate。`/api-hub --serve`でHTTP APIと1本の通知配送task、PgPool共有3、終了猶予90秒 |
| media | `--serve`（カタログ）、`--worker`（取得）、`--plan [YYYYMMDD]`（JST前日が既定）、`--reconcile`（毎時の期限整理）、`--verify-objects`（日次の存在確認） |
| hitomi | DB必須の通常収集をJST 02:55/06:55/10:55/14:55/18:55/22:55。`--dispatch`は永続受付まで |
| camera | 単一StatefulSet/PVCの別containerで`--record`/`--upload`。JST 19:00〜翌09:00、DB不要 |

api-hub/media/hitomiのmigrationはPreSync Jobで、migrationだけを実行する。cameraにmigrationはない。`--import`は[明示Job](../automation/examples/media-import/README.md)で初回移行・復旧時だけ実行し、通常同期からは起動しない。import自体は取得Jobを作らず、元keyを維持する。

共有DBは既存app資格情報・verify-full・CAを使う。[接続予算](shared-postgresql.md)のapp80/クラスタ100を維持する。cameraの退役DBとmigration履歴は保持し、物理削除しない。

## 配布と動作確認

公開は同一commitの回復・互換試験成功を必須にし、Image Updaterが検証済みlatestのdigestをGitへ書き戻す。実際のPod imageIDとKustomize digestを照合する。APIと配送を一緒に更新するため、Recreate中の短いAPI停止は既存HTTP再試行で扱う。

- api-hubの202は永続受付。hitomiのacceptedは終端であり継続GETをしない。Slackのsent/failed/uncertainと手動retryはapi-hubが管理する。
- HTTP受付と新claimはSIGTERMで停止する。進行中HTTP最大80秒、通知最大75秒を待ってから共有poolを閉じる。workerの予期しない終了はAPIも停止しprocessを異常終了する。一時的なDB/S3/Slack障害は再試行・metricsで扱う。
- PDF/動画の直接S3配信、Range206/304/416、条件付きGET、署名の再発行を確認する。preview/uploadはstreamを維持する。URL正規化で署名targetが変わる要求は送信しない。
- mediaは項目単位のintent/lease/retryを維持する。期限切れ・取得元失敗は一括再取得せず、必要な対象だけ明示再試行する。元ID不明のlegacy動画を順位で推測しない。
- hitomiは初回sourceだけbaseline、以後はDBの恒久ID/revisionで新規判定する。RSS障害時も既存pending/retryを処理する。1回50件/feedと2時間期限を守り、滞留・failedを監視する。
- cameraの完成mp4+manifestが未送信の正本。S3 HEAD/key/sizeとローカルchecksumを確認してから解放する。不正manifest/破損動画/両側欠損は保持し、推測でmanifestを再生成しない。spool上限80GiB、PVC100GiB、空き2GiB未満では未送信を消さず録画を止める。

空キュー・時間帯外待機を実業務の成功と混同しない。[業務キュー監視](application-queue-monitoring.md)を参照。

## DB復元

1. 統合api-hubとhitomi dispatcher/収集、mediaの書き込みを停止し、既存pgBackRest手順でDBを復元する。別媒体backupは今回の対象外。
2. schema、資格情報、TLS、source/items/outbox/catalogの状態を確認し、migrationを適用する。cameraのPVC/S3は移動・削除しない。
3. 通常の統合api-hubを起動して配送を再開し、hitomi/mediaを再開する。個人サーバーの運用としてDB巻戻りによる通知再送・重複は許容する。Slackとの事前照合やAPI専用modeを再開条件にしない。
4. 通常の冪等受付とuncertainの手動対応は維持する。sending/uncertainを無条件にpendingへ戻したり、全件再送したりしない。

mediaの毎時・日次処理分離とローカル一時領域への移行は[測定・反映手順](media-storage-optimization.md)を参照。
