# hitomi・mediaの失敗整理（2026-10-28 JST）

実装単位①原因記録・履歴保存、②管理操作、③通知・運用を本番へ反映した。

## 配備した変更

| 対象 | ソースcommit | 配備digest |
|---|---|---|
| hitomi | `5e16747` | `sha256:bed4a6c1ebdd41fd2eca4a3b3d2a72f1b216d91899e44412d26e937b3ce65958` |
| media | `98ba400` | `sha256:32319e1ae62a5b195807ecdb6e79ad1651adbe6e07d0fa3d067aed40de7a6e3f` |
| viewer | `98ba400` | `sha256:c0bee7cf79aed0dbc47afb31aa620aba6839ca604c90f4be806e61683ceb80da` |
| 通知ルール | `b2cd8ed` | observabilityのConfigMapとvmalertをGitOpsで更新 |

hitomi/mediaはPreSync Jobでmigration 3を適用し、Image UpdaterのGit書戻しとArgoCDで配備した。直接のimage差替えは行っていない。

## 原因が分かっている失敗

| 対象ID | 初回整理時の扱い | 調査結果・次の対応 |
|---|---|---|
| hitomi 6839 | 確認済み、failedのまま | タグ取得待機時間超過。galleryinfoとDOMの反映・待機条件を調査・修正して限定再実行 |
| hitomi 12859, 12860, 12863 | 確認済み、retryを継続 | タグ要素の文字列が空。取得元データとDOMの状態を調査・修正して限定再実行 |
| media 26893, 26897, 26898, 26899 | 確認済み、failedのまま | TwiHubのリダイレクト過多。URL解決・追従処理を修正した後、この4件だけ再実行 |

確認理由と次の対応を各失敗記録へ保存した。mediaは実際のfailed件数4を維持したまま、unreviewed=0 / acknowledged=4となることをAPI・画面・VictoriaMetricsで確認した。取得元消失の根拠がないため、対象外には変更していない。上記の根本原因の修正は別作業である。

旧DBに実際の失敗日時がなかったため、移行された記録の初回日時は移行時の観測日時。移行前に削除されたエラーの復元は行えない。

## 原因不明だった3件の個別再実行

失敗ID・versionを確認して1件ずつ理由付きで再受付し、指定item IDだけを処理した。通常収集・新着発見・他IDのclaimは行っていない。

| item ID | 再実行Job | 結果 |
|---|---|---|
| 2525 | hitomi-recover-2525-20261028-v2 | タグ未登録を確認し、既存ルールどおりfiltered / no-tags-v1 |
| 3389 | hitomi-recover-3389-20261028 | タグ未登録を確認し、既存ルールどおりfiltered / no-tags-v1 |
| 3396 | hitomi-recover-3396-20261028 | タグ未登録を確認し、既存ルールどおりfiltered / no-tags-v1 |

これは現在の再処理結果であり、詳細の失われた元の失敗原因を断定するものではない。旧失敗履歴は閉じて保持する。

最初の2525用Jobではコンテナcommandを指定し忘れ、起動前に停止した。itemはpending / attempts=0のままで、業務処理・通知は発生していなかった。commandを補ったv2で完了し、誤起動Jobは削除した。運用JobではDockerのCMDに引数を追加する際も、Kubernetes側に次を明示する。

```yaml
command: [/app/hitomi]
args: [--process-item, '2525']
```

## 検証

- Rustの通常テスト・APIテスト、隔離PostgreSQLでの移行・レビュー・再実行・保持期限・claimのテストが成功。
- viewerのビルドとブラウザテスト、理由必須・選択IDとversion・途中競合の表示を確認。
- promtoolの全ルールテスト、監視のPythonテスト7件、yamllint、Kustomize描画、kubeconformの39リソース検証が成功。
- GitHub Actionsのhitomi/mediaのbuild・recovery-tests・pushが成功。
- 本番Gateway経由の管理画面で、確認済みのmedia 4件の表示を確認。
- 新規失敗の検知・確認での解除・再発での再通知をルールテストで検証。旧exporterへのフォールバックも維持。


初回整理後の状態はhitomi_itemsのunreviewed=0 / acknowledged=4、hitomi_sourcesのunreviewed=0、mediaのunreviewed=0 / acknowledged=4。対象外は0件。取得系の未確認項目の最古滞留時間は両キューとも0秒。3件の個別再実行JobはすべてCompleteで、hitomi / hitomi-pdf / hitomi-upload-viewer / observabilityはSynced / Healthyを確認した。

最終評価でApplicationQueueFailed / ApplicationUnreviewedFailure / ApplicationQueueBacklogはすべてhealth=ok・inactive・alertsなしとなった。生のfailed件数を0へ書き換えた結果ではなく、確認状態を通知へ反映した結果である。

## 追加調査と根本修正（同日10:40 JST以降）

### hitomi：4件の対応完了

現行SeleniumでタグDOMとgalleryinfoを確認し、12859 / 12860 / 12863はタグが存在することを確認した。古いエラーは既存のtextContent取得・描画待ち修正前の記録だった。既存実装に対する実ブラウザテスト `selenium_distinguishes_untagged_from_unloaded_pages` も成功した。追加の取得方法や待機時間延長は不要だった。

失敗ID 5 / 6 / 7をversion 2で理由付き再受付し、`hitomi-tags-12859-20261028` / `hitomi-tags-12860-20261028` / `hitomi-tags-12863-20261028` で個別実行した。すべてJob Complete、attempts=1、filtered / ng-tags-v1 / NG tag matched。タグ未登録として除外したものではなく、タグを取得して既存NGルールを適用した結果である。3件の失敗エピソードは閉じて履歴を保持する。

6839（gallery 4283373）は、HTML自体のHTTP 200だけでは消失を判定できなかったが、ブラウザで404 Not Found表示とgalleryinfo欠落を確認し、メタデータ `galleries/4283373.js` もHTTP 404だった。失敗ID 3 / version 2へ証拠を理由として記録し、excludedに変更した。成功状態には書き換えていない。

この4件について未確認・確認済みの未完了失敗は0、理由付き対象外が1件となった。

### media：URL解決の修正と上流の自己転送

ソースcommit `1945416` で、TwiHub / Monsnodeの相対動画URLを転送後の最終ページURLに対して解決するよう修正した。従来は転送前のドメイン・ディレクトリを使っていた。HTTPクライアントは循環を検知し、転送経路をエラーへ記録する。転送上限10段とReferer抑制は維持した。

4件の投稿ページは新ドメインへ転送後HTTP 200となるが、そこに公開された動画リンクはGETで次の自己転送を返すことを確認した。

| asset ID | 動画リンク（twihub.link） | 応答 |
|---|---|---|
| 26893 | /r/v/2114695425534161099 | 302、Locationは同じURL |
| 26897 | /r/v/2114718539563360703 | 302、Locationは同じURL |
| 26898 | /r/v/2114395949267296678 | 302、Locationは同じURL |
| 26899 | /r/v/2114034912412008602 | 302、Locationは同じURL |

最終URLを使う修正で旧ドメインへの不要な往復はなくなるが、取得元側の自己転送は解消できない。消失は確認されていないため、media 4件は対象外にしない。

ローカルRustテストは24件成功。別origin・別ディレクトリへの転送、相対/絶対URL、リンク欠落、404、自己/2点循環、上限、正常な複数段転送を追加検証した。既存のReferer・ダウンロード・形式検証テストも成功。

GitHub Actions `40616626105` の通常・DB回復テスト、viewerテスト、イメージsmoke test、Harbor公開がすべて成功。Image UpdaterのGit書戻し `ec6a9f8` とArgoCDでAPI / workerへ `sha256:11ff39e8dc216ff70bb6e3d303e82f1c5aa312c569a53c74f323530b46972b2f` を配備した。

10:47 JSTに旧失敗ID 2 / 3 / 1 / 4を対象別に再受付し、4件とも新しいワーカーで再実行した。すべて新ドメインから取得を開始し、同一URLへの自己転送で停止した。新しい失敗IDは順に5 / 6 / 7 / 8、cause=redirect_loop、stage=source_fetch。エラーに `https://twihub.link/r/v/{ID} -> https://twihub.link/r/v/{ID}` を保存し、各記録を理由付きで確認済みにした。

現在はasset=pending / job=retry / attempts=1で、既存の再試行・8回打ち切りを維持する。成功やreadyへの書換えは行っていない。同じ原因の自動再試行では確認済みの扱いを保持する。再開条件は、取得元の公開動画リンクが自己転送を解消し動画を返すこと。その時点で、この4件を対象限定で再実行する。

最終確認でmediaはunreviewed=0 / acknowledged=4 / excluded=0、未確認の最古滞留時間は0秒。ApplicationQueueFailed / ApplicationUnreviewedFailure / ApplicationQueueBacklogはhealth=ok・inactive。hitomi / hitomi-pdf / hitomi-upload-viewer / observabilityはすべてSynced / Healthy。hitomiの4件は対応完了、mediaのアプリ修正は配備済みだが4件の動画取得は上流障害のため未完了である。
