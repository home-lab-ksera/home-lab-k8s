# 2026-10-25 配備確認

PR #108〜#115の段階移行を反映した時点の記録。関連ApplicationはSynced/Healthy。
本番構成の有効化と、実業務の全工程を確認済みであることを区別する。
共有app role・既存資格情報を使用し、別媒体backupは利用者指示どおり対象外。

| 対象 | 配備・実機で確認した状態 | 残る確認 |
|---|---|---|
| api-hub | migration 1/2成功、API/通知worker稼働、app/TLS。PDF・動画の署名GET/Range/CORSと旧proxyとの内容一致 | 実通知の受付→Slack配送→sent、実端末での再生開始・シーク・署名期限切れ |
| media/viewer | migration成功、API/worker/planner/reconcile稼働。既存3,313 objectのキー・サイズ一致。公開カタログ全ページ/random・実ブラウザ操作、不要な先読み0件 | 取得元403/429の失敗3件。転送/端末性能の一貫した改善は未実証 |
| hitomi | migration version 1成功、outbox 1/1 Ready、公開済みAVIF修正imageを適用。DBはapp/TLS、内部api-hub URL設定を確認。初回収集前でsources/items/outboxは空 | 22:55 JSTの実source別baseline、以後の新規項目、実AVIF処理と通知配送 |
| camera | migration version 1成功、旧CronJob停止・active 0、新Pod 2/2 Ready・再起動0。100 GiB PVC Bound、UID 1000で書込/両lock作成、app/TLS、health 200、VictoriaMetrics収集 | 19:00 JST以降の実カメラsegment確定・S3転送・spool解放と連続録画 |

cameraの12時台のmetricsはspool 0、blocked 0、partial 0、recording_active 0、restarts 0。
録画時間帯はJST 19:00〜翌09:00であり、この時点では待機が正常。実録画成功を意味しない。

hitomiの本番digestは `sha256:549f071fb2f42e1e2c37da5cb06ed21ac60de13f309fd40c022743e764b2a061`、
Cronは13:55 UTC（22:55 JST）。migrationの初回PodはDB接続poolの取得timeout後、Jobの再試行で成功した。
削除済みPodのログはLokiで確認した。最初の接続が遅れた要因は未特定。outboxの起動後ログにはエラーを認めず、DB接続を確認した。
通知キューが空のためoutbox→内部api-hub→Slackの実配送を確認済みとは扱わない。

本確認で実カメラの時間帯設定を変更したり、本番収集Jobを手動起動したり、検証用Slack通知を送ったりしていない。
Slackの配送結果不明は既存のuncertain手順で照合する。失敗したmediaを一括で再取得していない。

現行の運用は[運用手順](../application-postgresql-cutover.md)、計測結果は[S3計測](../performance/20261025/README.md)を参照。
