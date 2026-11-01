# mediaの照合頻度と一時ディスク

## 状態

2026-10-29: 本番反映、存在確認Jobの手動実行、ローカル一時領域での通常取得を確認済み。旧NFS一時領域への切戻し設定を撤去する。
アプリ変更は隣接するhitomi-pdf-batch、GitOps変更はmanifests/apps/hitomi-pdf/manifest.yaml。

## 測定根拠

2026-10-28 23:13 JST頃、media DBのSELECT集計とkubelet stats/summaryを読み取り確認した。
URL・object key・メディア本体は取得していない。DB台帳にsizeがある保存履歴の集計で、将来のファイルサイズは保証しない。

| 種別 | 件数 | 最大bytes | p95 bytes |
|---|---:|---:|---:|
| PDF | 1659 | 92502473 | 13661584 |
| preview | 1659 | 703359 | 363933 |
| video | 422 | 4446309238 | 1575624952 |

worker1/2のルートディスクは各30083776512 bytes、空きは7479345152 / 8897875968 bytes。
container/image filesystemも同じ空き容量だった。一時点の値で、CIやイメージ取得と競合する。
既存10GiBをそのまま許容する余裕はないため、本番MAX_ASSET_BYTESを5GiBに変更する。
最大実績約4.14GiBは上限内だが、取得時の空き容量によっては再試行になる。

## 変更

- `--reconcile`: 毎時20分。未完了キュー24時間整理、asset期限、古いintentの整理のみ。S3に接続しない。
- `--verify-objects`: 毎日04:40 JST。開始時の最大asset IDまで、期限内のreadyを500件ずつ全走査。
  404だけmissingにし、S3通信障害は失敗終了。閲覧時HEADによる404検出も維持。
- worker `/tmp`: NFS PVCからディスク型emptyDir 6GiBへ変更。request 5GiB / limit 7GiB。
  1 replica / Recreateを維持し、同じspoolを複数workerで共有しない。
- `SPOOL_MIN_FREE_BYTES=4294967296`: 既知のContent-Lengthと各書き込み前に空きを検査。
  4GiBを残せなければ中断・ファイル解放し既存の再試行へ戻る。空き容量の予約ではない。
  再試行は既存の最大8回、24時間整理の対象となる。恒常的な容量不足は失敗管理画面で確認する。
- 一時ファイルは専用ディレクトリのNamedTempFile。emptyDirの走査計測から見える形式にする。
  通常終了時は削除、強制停止で残った自分のファイルは次のworker起動時に削除。
- 旧NFS一時領域のPVC定義は撤去する。通常の再試行・lease/intentによる復旧は維持する。

## 反映順序

1. アプリのCI成功後、先に新imageを公開する。
2. Image UpdaterのGit write-backと実PodのimageIDを確認する。
   新CLIと空き容量チェックのない旧imageへ、このGitOps設定を先行適用しない。
3. GitOps変更を反映し、workerの/tmpがemptyDir、新上限・空き容量設定、日次CronJobのimage digestを確認する。
4. 次の通常取得で成功、ノードの空き、DiskPressure/eviction、spool残骸を確認する。
   次の毎時JobでS3照合がないこと、日次Jobで完了することを確認する。

worker切替時の未完了Jobは既存lease期限後に再試行する。検証目的で本番へ取得Jobを追加しない。
DB schema変更・S3移動はない。

## 検証

- `cargo test --locked -- --include-ignored`: ローカルの隔離PostgreSQL 18で28件成功。
- 毎時処理のHEADゼロ、501件以上の走査、閲覧時404、S3停止中の期限整理、通信障害でreadyをmissingにしないことを確認。
- 一時領域の空き不足判定、再起動時の残骸掃除、サイズ上限（Content-Length有無）、ストリーム保存と復旧を確認。
- `nix develop --command automation/scripts/ci/validate.sh`: 全体成功（Kustomize/Helm展開、kubeconform含む）。

ローカル一時領域での通常取得と存在確認Jobの手動実行は下記のとおり確認済み。本番性能の前後比較は未実施。


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
