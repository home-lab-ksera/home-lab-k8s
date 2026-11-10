# media・cameraアラート対応（2026-11-04 JST）

## media: ApplicationUnreviewedFailure

対象はasset `28708`（TwiHub動画 `2116741473479647499`）。11月4日01:01からHTTP 429と503が交互に発生し、03:15に8回目が503で失敗した。元の失敗記録は49 / 51 / 53 / 55。DB/S3の障害ではなく、動画取得段階で取得元から返されたHTTPエラーである。

ユーザーの対応指示に基づき、失敗ID 55/version 2からこの1件だけを再受付した。11:30以降の再実行もHTTP 429となり、失敗ID 57へ理由付きで確認済みを記録した。既存バックオフ・8回上限を維持し、成功・対象外へ書き換えていない。取得元復旧後の対象限定再実行が必要。原因分類が変わった場合は既存設計どおり再び未確認になる。

12:47の再確認時点で7回目もHTTP 503。原因分類が変わって失敗ID 63/version 1が未確認になっていたため、同じ取得元障害の継続であることを12:48に記録して確認済みとした。最終API値はunreviewed=0 / acknowledged=8、retry=1。動画自体は未取得で、通常の再試行が残っている。

## camera: CameraPartialSegment

11月2日04:14:40に`closed segment could not be finalized: segment is incomplete`。04:20からアラートが継続していた。Pod再起動は0。原本は次の1件。

`/spool/e8ec5b7e-c1cc-4405-ab4a-01e21f43db85/segment-1793560479.corrupt`

- サイズ: 31,011,111 bytes
- SHA-256: `e19b77d4a28a88da084ca9cc20618d8f77baaef9bb503a9e669816260cfacf1f`
- ffprobe: `moov atom not found`
- MP4のトップレベルboxはftyp / free / mdatのみ。moovがなく、通常のMP4として回収できない。
- CSVに同じ名前が`0.000000,5909.261979`と`5909.262370,7201.281315`の2区間として登録されていた。
- 破損扱いの後も04:36まで容量が増え、書き込みが続いていた。

原因は秒単位の`segment-%s.part`とCSVによる確定処理の競合。実FFmpegへ実時間待ちなしで入力すると7区間が1つの名前に衝突することを再現した。稼働時の入力側で何が時間跳躍・バッファ放出を起こしたかは、旧実装がFFmpegのstderrを捨てていたため確定できない。

[home-camera PR #10](https://github.com/home-lab-ksera/home-camera/pull/10)、ソースcommit `6c40b10d53bcf88549034ca9a4c527cb3d12fdb7`で修正した。

- session開始epoch＋連番でファイル名を一意化し、S3キーにも連番を付ける。
- CSVの改行完了行だけを読み、同名・不正パス・不正時刻を拒否する。
- 旧spool/manifestの回収互換を維持する。
- RTSP資格情報を伏せてFFmpegのエラーと再接続理由を記録する。
- 調査済み原本用の`.quarantine`を自動回収・送信・未確認segment数から除外する。自動で隔離・削除はしない。

通常テスト19件と実FFmpeg回復テスト4件が成功。本番と同じFFmpeg 5.1.9でも回復テスト4件が成功した。重複防止、連続録画、S3停止時の保持、RTSP timeout、旧spool互換を確認した。新形式の表示時刻はsession開始UTC＋メディア時刻であり、入力時刻の跳躍は表示には影響し得る。

## 原本保全

ローカルへ複製し、移動前後と複製先のSHA-256一致を確認した。原本・CSV・調査理由は以下に保持している。

- PVC: `/spool/.quarantine/20261104-e8ec5b7e-c1cc-4405-ab4a-01e21f43db85/`
- ローカル: `/home/ksera/.local/state/home-lab-incidents/20261104-camera/`

破損動画の復元・S3送信が成功したとは扱わない。11月4日09:01に通常分のspool解放は確認済み。今夜の実カメラによる録画は別の観測となる。

## 配備・最終確認

[公開CI 41761999104](https://github.com/home-lab-ksera/home-camera/actions/runs/41761999104)は全ジョブ成功。11:54にHarborへ公開し、Image UpdaterによるGitOps commit `ed6bb9035652fd6dd6b3405eeb5967e1c98e126f`で更新された。

配備digest: `sha256:b034e3eeaa1d2101b27a47288d527feaf99cbc4d3a457079b88853a731eeebe1`

11:58:27に新Podが作成され、11:58:49からArgoCDはSynced/Healthy。12:48の確認でrecorder/uploaderともReady・再起動0、Podの実imageID・spec・ArgoCDのimage・公開CIのdigestが一致した。partial=0、invalid_manifests=0、spool_bytes=0、recording_stalled=0、stats_success=1。時間帯外なのでrecording_active=0。保管ファイルのSHA-256も配備後に一致を再確認した。

12:49にCameraPartialSegment / ApplicationUnreviewedFailureの両方がhealth=ok・inactiveとなり、Alertmanagerの対象アラートも消えたことを確認した。mediaは未確認0・確認済み8・retry1で、取得元障害は継続している。録画時間帯の実カメラによる継続確認はまだ実施していない。


## 11月5日: 429・503の追跡方針変更

ユーザーの指示により、取得元が返す429・503は想定内として失敗追跡から除外する。[media PR #7](https://github.com/home-lab-ksera/hitomi-pdf-batch/pull/7)をマージした（`ff2f7c749bb0ba1fe485313567005c5a2bfee9e9`）。11月4日に行った個別の確認済み処理を、この恒久方針へ置き換える。

- 対象は`source_fetch` / `source_resolve`の429・503。保存先503や他のエラーは従来どおり検知する。
- バックオフと最大8試行を維持し、Retry-Afterも尊重する。上限到達時は取得成功でも失敗でもない`unavailable`（取得見送り）で終了する。
- migration 5で既存の該当失敗を終了し、理由・履歴を保持する。429・503が交互に返っても新たな失敗エピソードを作らない。
- 画面では取得見送りと表示し、手動で再取得できる。
- ローカルのRust 34テスト（DB移行・実HTTPを含む）、Node 10テスト、viewer build、catalog/failure/cameraブラウザテストが成功。PRのCIも全件成功。

公開CI [41998614243](https://github.com/home-lab-ksera/hitomi-pdf-batch/actions/runs/41998614243)は再実行後に全ジョブ成功。画面側の公開ツール取得が一度HTTP 504で失敗したため、その公開ジョブだけ再実行した。

GitOps commitはbatch `152223f7ead36c3098df90b509447798db749e46`、viewer `01ddeb0adfc73601f553487e7bb2c4f53655b178`。18:13:33 JSTにPreSync migrationが成功した。

- batch digest: `sha256:2755d3531129ee9c3efe3b544a7612b69675f526418ac1778e987326d1b4b268`
- viewer digest: `sha256:fa45fb174d168af521a5ca306a5742a9770f10caf0effcaa32609d823f83ed20`
- asset 28708は`unavailable`。failure 63は18:13:33 JSTに追跡終了し、以前の理由と方針変更理由を保持。履歴APIでも5件の証跡を確認した。
- 失敗一覧から対象503が消え、別件の未確認403が1件残ることを確認。403は今回の対象外であり、確認済み操作はしていない。
- ArgoCDは両ApplicationともSynced/Healthy。API・worker・viewerは公開digestと実imageIDが一致し、Ready・再起動0。

429・503は今後も想定内応答として失敗追跡から除外される。動画の取得成功とは扱わない。
