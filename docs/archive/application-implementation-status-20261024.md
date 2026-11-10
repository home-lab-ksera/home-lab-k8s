# アプリケーション構成見直しの実装状況

承認済みの方針は [構成レビュー](application-architecture-review-20261024.md)。既存の共用 `app` と資格情報を使い、追加アプリの提案は対象外。
PR単位の目標照合、監査で修正した問題、未達成の本番条件は [受け入れ確認](application-architecture-acceptance-20261024.md) を参照。
アプリの実装、ローカル検証、切替用GitOpsマニフェストと運用手順を作成済み。5リポジトリのPRはmergeされ、DB・資格情報・CA・S3配信経路の基盤も反映された。api-hubのmigrationと通知workerは配備・動作確認済み。mediaのmigration・API/worker・既存S3 importも完了し、3,313 objectのキーとサイズを照合済み。hitomiのmigration/outboxとcameraのrecorder/uploaderも配備済み。4アプリのDB切替が反映された。
既存RTSP値からの `sandbox/home-camera` Secret作成も完了。値は出力していない。viewer切替・planner再開も配備済み。最新の状態と残る実運用確認は [配備確認](application-deployment-verification-20261025.md) を参照。

## 実装した部分

- S3 continuation tokenをapi-hubまで渡し、繰り返しページ・トークン欠落をバッチとviewerで検出する。
- PDF/MP4の変更前測定を実施。[変更前測定](../performance/20261024/README.md)と[新S3 HTTPS経路の機能・速度確認](../performance/20261025/README.md)を記録。一貫した速度改善はまだ確認できていない。
- 内部S3 HTTPSのHTTPRoute/証明書/DNS/NetworkPolicyと、viewer用CORSの宣言を追加。
- api-hubのブラウザ向け署名を `pdfs/hitomi/` のPDF/JPEGと `pdfs/videos/` のMP4に限定。900秒のGET URLを発行し、Rangeを固定しない。
- viewerの動画一斉preloadと、JPEG欠損時のPDF本体読み込みを抑制。実MP4を使ったブラウザテストで署名再発行・再生位置復元を確認。
- `api_hub` / `hitomi` / `media` / `camera` DB、既存資格情報・CAのsandbox配布、接続用NetworkPolicyを追加。app接続予算は80、全体100を維持。
- api-hub通知キュー：永続化後202、冪等受付、429待機、送信結果不明、手動再試行。S3画像のSlack共有とfile ID保存、OpenAPI更新も実装済み。
- media：planner/worker/API/import/reconcileの起動モード、安定した動画ID、試行ごとの保存キー、DBカタログ、欠損の個別再取得。PUT成功後DB更新前の停止からの回復テストを実施。
- viewerにカタログ画面、状態・未読絞り込み、再取得・取得状況表示を追加。従来画面と切替可能。
- hitomi：初回baseline、項目claim、NG判定の保存、画像S3保存と通知outbox、漫画の更新期間を含めた重複排除。隔離DBでbaseline・再送・古いclaimの拒否を確認。
- camera：常駐segment録画、PVC上のmanifest、別uploader、DB障害時も録画できる構造、spool容量制御・監視値・アラートを実装済み。
- migration/workerを含むCI、[運用手順](../application-postgresql-cutover.md)を追加。planner停止・camera 0 replicaで準備し、旧writer終了確認後に順次有効化した。

## 検証結果

| 対象 | 確認結果 |
|---|---|
| api-hub | 通常テスト34件、隔離DBの冪等受付・同時claim・429・lease回復・手動再試行。画像共有成功と結果不明をmockで再現しfile IDを確認 |
| hitomi | AVIF修正後のlib 57件、HTTP 3件。実AVIF→PNG/JPEGも成功。隔離DBでbaseline、RSSから消えた項目、outbox再送、配送状態照合、古いclaimの拒否を確認 |
| media | 通常テスト24件、隔離DBで安定ID、preview個別修復、PUT後DB更新前の回復、checksum、直接配信とproxy切戻しを確認 |
| camera | 通常テスト18件。追加で隔離DB/S3停止時の復旧と、実FFmpegによる未送信segmentを残したままの継続録画を確認 |
| viewer | APIテスト7件、production build、実MP4 fixtureのRange応答・署名再発行・再生位置復元、カタログ操作、PDF本体の不要な取得抑制を確認 |
| コンテナ | API/batch/viewer/hitomi/cameraの5 imageをローカルビルド。4アプリのimage内migrationが専用DBで成功（API 2件、他各1件）。batch/viewerのsmoke test成功 |
| Kubernetes/CI | 全体validate、DB権限・接続許可のテスト、NetworkPolicy、manifest整合性、alert test、4つの変更workflowのactionlintが成功。切替用例もYAML/schema検証済み |

検証用のSlack実送信は行っていない。署名期限切れ・動画再生位置のブラウザ回帰は生成したfixtureを使用。本番ブラウザでは一覧・ページ送り・random・先読み抑制を確認し、実メディアの再生操作は行っていない。
ローカル検証時のimageタグは `architecture-20261024`。merge後の5 imageはHarbor公開CIが成功し、Image Updaterが本番kustomizationへdigestを記録した。

## 配備後の確認

viewerの公開HTTPRouteはAccepted/ResolvedRefs、関連3 ApplicationはSynced/Healthy。公開経由でPDF 1,509件・動画295件の全ページとrandom、署名URLのRange/CORSを確認した。実ブラウザで一覧・ページ送り・random表示が成功し、S3 List 0件・PDF本体の先読み0件・再生前の動画GET 0件・JavaScriptエラー0件を確認した。動画の実再生/シークと署名期限切れの本番確認は残る。

00:50 JSTのmedia plannerは成功（20261024、expected 160、planner_errors 0）。継続確認ではingest_jobsはdone 41、failed 3。失敗は取得元の403/429で、全件成功とは扱わない。

hitomiとcameraのmigration・各workerが稼働し、4 DBへのapp role/TLS接続を確認した。詳細は [配備確認](application-deployment-verification-20261025.md)。

## 本番で残る確認

- cameraは19:00 JSTからの実segment、S3転送、spool解放を確認する。hitomiは22:55 JSTのsource別baselineと、その後の新規項目・outbox配送を確認する。
- mediaの取得元403/429による失敗3件を確認する。未確認のまま全件再取得は行わない。
- 新S3 HTTPS経路の配備後、実端末からPDF/動画取得・シーク・CORS・署名期限切れを確認し、同一条件で再測定する。現在の結果から新経路の改善率は断定しない。
- 別媒体backupは利用者の指示で今回の対象外。切替を妨げる未完了項目には含めず、既存backupと復元手順を維持する。

## 検証用リソース

本番計測用Pod/NetworkPolicyは削除済み。隔離PostgreSQL（`/tmp/api-hub-pgtest.V5VpRX/data`、127.0.0.1:15433）とviewer検証サーバー（127.0.0.1:5174）は検証後に停止した。
ローカルDBのデータと検証用imageは残している。本番DBへのmigrationはapi_hub・hitomi・media・cameraの4 DBで完了。
