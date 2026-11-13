# media 一時ディスクの本番反映（2026-10-29）

[mediaの照合頻度と一時ディスク](../media-storage-optimization.md) から移した反映・確認の記録。

## 本番反映記録（2026-10-29）

- アプリ: [PR #6](https://github.com/ksera524/hitomi-pdf-batch/pull/6)、commit `2932821`。
  [公開CI](https://github.com/ksera524/hitomi-pdf-batch/actions/runs/40706725328)成功。
  公開digestは `sha256:00c493e9ee33c8905b957057a678ce444325d707e3e914807cb5a2fcd47f83ec`。
- Image Updaterのwrite-backは `6a6087f`。API/worker実imageIDとの一致を確認してからGitOpsを反映。
- GitOps: [PR #130](https://github.com/home-lab-ksera/home-lab-k8s/pull/130)、commit `9b1f046`。
  PR全CIと[マージ後CI](https://github.com/home-lab-ksera/home-lab-k8s/actions/runs/40780662204)成功。
- ArgoCD hitomi-pdfはSynced / Healthy、同期操作Succeeded。
- 新worker `media-worker-667696ff4f-6cpv4` は07:32:32 JST起動、Ready、再起動0。
  実Podの/tmpはemptyDir 6GiB、MAX_ASSET_BYTES=5368709120、SPOOL_MIN_FREE_BYTES=4294967296、
  ephemeral-storage request/limitは5GiB/7GiB。公開digestとの一致を確認。
- API `/health` `/ready` `/metrics` はport-forward経由でHTTP200。
- 毎時CronJobは20分、日次は04:40 JST、両方Forbid/suspend=false。
  新imageで07:20 JSTに実行した毎時JobはSucceeded、ログは `media queue retention: removed 0 stale jobs`。
  日次Jobは今回新設のため未実行。次回は2026-10-30 04:40 JST。
- 初回反映時は旧media-worker-tmp PVCを保持した。動作確認後に定義を撤去する（下記）。
- 初回反映時は試験取得・通知・日次確認の手動投入なし。その後、依頼により存在確認だけ1回手動実行した。

### 残る容量制約

07:32頃のworker2空きは5374558208 bytes（約5.0GiB）、imageFs使用量は約13.8GB。
前日の測定より空きが減っている。worker自身のephemeral使用量は28672 bytesだった。
空き4GiBを残すため、現在は約1GiBを超える取得が再試行になり得る。
5GiBは許容上限であり、常にそのサイズを取得できる保証ではない。
大きいファイルを継続取得するにはノードの空き容量確保が別途必要。キャッシュ削除・VMディスク拡張は今回行っていない。

確認時のingest_jobsはdone539/failed5。確認済み失敗4件のほか、00:53〜03:14 JSTに発生したsource_fetchの未確認失敗1件がある。
これは07:32の一時領域切替より前の記録で、今回失敗行を成功扱い・再試行・削除していない。


## 動作確認と移行用設定の撤去

2026-10-29 08:49:44〜08:50:34 JST、`media-verify-manual-20261029` を日次CronJobから作成して1回実行。
Succeeded / exit 0で、ready 3364件すべてのlast_verified_at更新を確認。欠損・新規エラーなし。
切替後の通常取得は動画12件、最大967528453 bytes（約923MiB）、worker再起動0。

これらの確認を受け、旧 `media-worker-tmp` PVC定義と今回の旧構成への切戻し手順を削除する。
削除前に旧PVCを参照するPodが0件であることを確認済み。ArgoCDのpruneでPVC/PVを撤去する。
StorageClassの既存方針はarchiveOnDelete=trueなので、NFS上の旧ディレクトリはprovisionerによるアーカイブ対象となる。
共有StorageClassの保持方針は変更しない。通常のエラー再試行、起動時の一時ファイル掃除、lease/intent復旧は現行運用として維持する。
