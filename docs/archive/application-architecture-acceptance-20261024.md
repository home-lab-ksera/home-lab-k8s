# 承認済み目標とPRの受け入れ確認

対象は [構成レビュー](application-architecture-review-20261024.md) の完了条件。
以下は実装PRレビュー時点の監査記録。merge後の配備状況と追加の実測は [実装状況](application-implementation-status-20261024.md) を参照。
既存の共用 `app` 資格情報を使い、追加製品の提案は含めない。別媒体backupは利用者の指示で今回の対象外とし、未達成条件から除外する。
**コードと自動テストの確認と、本番での目標達成は別である。** 以下の表は実装PR監査時点の記録。4アプリの配備は完了し、残る実運用・性能確認は [2026-10-25の配備確認](application-deployment-verification-20261025.md) を参照。

## 目標との対応

| PR | 実装・検証で確認したこと | 本番で残る確認 |
|---|---|---|
| [api-hub #11](https://github.com/ksera524/api-hub/pull/11) | PDF/preview/MP4だけに署名、900秒、有効なRange要求、opaque continuation token。通知の永続受付・冪等性・429・結果不明・手動再試行・画像共有を隔離DB/mockで検証 | 新S3 HTTPS経路のCORS/Range、実端末のPDF初回表示・動画開始/シーク時間。Slackの実配送確認 |
| [hitomi #24](https://github.com/ksera524/hitomi/pull/24) | 初回baseline、安定ID、RSSから消えた未完了項目、lease、原子的outbox、再送と配送状態の照合を検証 | 旧Job終了後の切替と実sourceの初回baseline件数、通知済みへの遷移 |
| [hitomi-pdf-batch #1](https://github.com/ksera524/hitomi-pdf-batch/pull/1) | 安定した動画ID、項目単位の再試行、PUT後DB更新前の回復。DB一覧/ランダム、個別修復、既存keyを保持したimport、期限表示、署名更新と再生位置復元を検証 | 本番S3のimport件数照合、カタログAPI p95、実端末での速度比較 |
| [home-camera #6](https://github.com/ksera524/home-camera/pull/6) | 実FFmpegのsegment継続、DB/S3停止中のspool保持、復帰後再送、二重起動防止、破損隔離を検証 | 実カメラ・PVCでの連続録画、spool監視、実機のkeyframe間隔とsegment長 |
| [k8s_myHome #108](https://github.com/home-lab-ksera/home-lab-k8s/pull/108) | 4アプリDBと共用app、CA/資格情報配布、双方のNetworkPolicy、S3ルート/CORS、切替用workload。manifest全体検証と接続予算を確認 | 公開済みimageのdigest照合とworkloadの段階的切替 |

100件超のページ送りはviewer APIテスト、署名再発行とRangeはAPIテストと実MP4 fixtureのブラウザテストで確認した。実ブラウザの試験でもS3/Slackの本番経路を再現したとは扱わない。
カタログと通知の追加テストは隔離PostgreSQLを使い、SQLをmockせず実行している。カメラの障害試験も録画fixtureと隔離DBを使う。

## 再確認で見つけて修正した点

1. PostgreSQL runtime CIが旧版クラスタへ新版bootstrap SQLを適用し、未作成のDBで失敗していた。対象版のSQLを使い、PGOのDB作成完了も待つよう修正。更新・再起動・復元後に全アプリDBのapp接続、TLS、所有者、PUBLIC ACL、DDLを検証する。
2. hitomiの既存APIテストworkflowにNASMがなく画像codecのビルドが失敗していた。依存を追加し、無効なrust-cache入力を除去した。
3. ランダムAPIにviewerの表示条件である `state` がなく、カード本体を表示できなかった。応答を補い、実DBのランダム応答に対する回帰テストを追加した。
4. 既存PDFのimportで取得元URLと欠けたPDF/previewの台帳が不足していた。既知の日付・連番からURLを登録し、不足分をmissingとして残す。二度のimportで重複せず、既存PDFを再取得せずpreviewだけを取得できることを検証した。import自体は取得ジョブを作らない。
5. 接続予算からimport・reconcile・資格情報確認が漏れていた。Harbor/Grafanaと同時rolloutを含む見積もりを78に修正した（app上限80、クラスタ100）。

## 実装PR監査時点で残っていた条件

- **性能改善の実証**：変更前の計測はあるが、新S3 HTTPS経路の同条件比較と端末描画・再生開始、カタログAPI p95は未計測。改善率・目標値への到達を主張しない。
- **本番切替**：新workloadはArgoCD参照外の切替用例にあり、4アプリDBへの本番migrationも未実施。これらのPRのmergeだけで全アプリが新構成になるわけではない。

別媒体backupは対象外。既存のpgBackRestとkind内のbackup/restore試験は維持する。

現行運用は [移行手順](../application-postgresql-cutover.md)、計測条件は [変更前測定](../performance/20261024/README.md) を参照。
