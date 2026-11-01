# Harbor 3世代保持の初回反映記録（2026-10-28）

現在は後半の「自動保守への切替」まで反映済みです。前半は初回pilotの履歴です。

## 反映内容

- 旧 `harbor-sandbox-image-cleanup` CronJobと所有JobがGitOpsのpruneで撤去されたことを確認。
- sandbox project（ID 2）のquotaを無制限から4 GiBに設定。反映時使用量は911,594,927 bytes。
- api-hubを先行対象とし、current・previous（確認できる場合）・candidateの最大3 digestを保護。
- 他8 repositoryはuntagged artifactを含め全保持。独立した定期保持実行は設定しない。
- api-hubの公開workflowにbuild ID/attempt/commit SHAタグとdefault branch HEAD確認を反映。

## 初回標準dry run

Harbor retention policy ID 1、execution ID 675がSuccess。
全9 repository・14 artifactがRETAINとなり、削除対象は0。
保持処理の実削除とGCは実行していない。

反映前のapi-hubはcurrentとlatestが同じ1 digestで、previousは不明。
確認済みの稼働版は次のとおり。

```text
sha256:ca619f4908f60cb2ab00e9c801d792171d22a4db1f7bbe11412a0e6e438f0a95
```

以後の保守では、正常稼働確認後の新digestとこの記録を用いてpreviousを指定する。
手順は [保持契約と保守コマンド](harbor-retention.md) を参照。

## 実環境で補正したAPI互換性

HarborがGETレスポンスで省略するpriority=0、disabled=false、空paramsを正規化する。
非定期実行はAPIのtrigger.kindを空文字にする。Manualを指定したpolicyの更新は実APIで拒否されるため使用しない。
これらを反映した15件の保持回帰テストが成功。

## 新workflowの公開確認

[api-hub run 40628925210](https://github.com/ksera524/api-hub/actions/runs/40628925210) が成功。
GitHub側のビルド・テスト、ARC側のversioned push、HEAD確認、latest昇格がすべて成功した。
作成されたタグは `build-40628925210-1-902dea75d2d4956d98aeb9b3b9d7df4ebd1f118d`。
新しいdigestは次のとおり。

```text
sha256:7106c516c588989572cd65d6506fb50bf272a44b322ae665de920d3e4820614f
```

公開後の保持対象はこの候補と上記の旧稼働版の2 digest。削除対象は0。
sandbox使用量は917,006,102 bytes。4 GiBの上限を維持。

公開後の標準dry runはexecution ID 676でSuccess。
GitOpsでImage Updaterとapi-hubの自動同期を復帰し、公開workflowも再開した。
自動同期は一時停止のfalseが残らないよう `enabled: true` をGitで明示した。
Image Updaterの自動書き戻しcommitは `c383faa64421dda55fa0dbe5b0767048d69f8d66`。
新版へのrollout完了、ApplicationのSynced/Healthy、PodのReadyを確認。
最終的なcurrent/candidateは新版、previousは上記旧稼働版で、保持対象は2世代・削除対象0。
公開後のdry runでは全15 artifactが保持された。

## 自動保守への切替（同日、後続対応）

上記は手動pilot時点の記録。以後の正本は [自動保守の手順](harbor-retention.md) と
`manifests/platform/harbor/maintenance.json` で、旧手動ツールは撤去した。

- 全8アプリを対象に、5分ごとの`harbor-maintenance` CronJobを有効化。常駐Podの追加なし。
- CIの`latest`と配布用`deploy`を分離。Image Updaterの全8参照を`deploy`へ変更。
- 正常版・直前正常版をHarborの固定役割タグに保存。失敗中はそのrepoを全保持し、明示した正常版への切り戻しだけを許可。
- sandbox quota 4 GiB、90%で通知。Harbor標準GCを毎日03:30 JST、workers=1、`delete_untagged=false`で設定。
- 保守成功が30分以上なく10分継続、またはGC実成功が36時間以上ない場合に通知。

初回JobはArgoCDの既定Lease除外により失敗したため、Leaseを実行時に初期作成するよう補正した。
管理対象workloadの書込権限は与えず、専用Leaseの取得・更新とLease作成だけを許可する。
HarborのGC設定GETは`dry_run=false`を省略するため、省略値をfalseとして比較して不要な予約更新を防いだ。
いずれも回帰テストを追加済み。

### 実環境検証

隔離project `maintenance-verification` と一時Deploymentで、4つの異なるイメージの起動を確認。
5つ目は存在しない実行ファイルを指定して意図的に失敗させた。
失敗時にcurrent/previousが変更されないこと、削除が見送られること、正常な4版目への切り戻しを確認した。
最終的に3版目・4版目・失敗した候補の3 digestだけが保持された（標準保持execution 704、Success）。
この検証は実Harborと実Kubernetesを使用し、ArgoCDの成功ステータス入力は検証用に与えた。
本番のArgoCD連携は、blog・cooklog・home-camera等の実際のGit書き戻しとSynced/Healthyで別途確認した。
一時Deployment・検証project・検証イメージは撤去済み。

本番の初回実保持はexecution **707**、9 repositoryすべてSuccess。
19 artifactから14 artifactへ整理し、hitomiの旧版2件、hitomi-pdf-batchの旧版2件、hitomi-upload-viewerの旧版1件を削除した。
正常稼働の証跡がない旧artifactをpreviousとして推測して残すことはしない。
api-hubの確認済みpreviousは引き続き保持した。

定期起動した`harbor-maintenance-29886285`は2026-10-28 17:47:08 JSTに成功。
その後の17:50、17:55の定期Jobも成功し、全8アプリeligible、blocked=[]を確認した。
17:55の保持executionは714（dry run）・715（実保持）、いずれもSuccess。
`kube_cronjob_status_last_successful_time`の実メトリクスを確認し、保守停止アラートは解消した。

GCはdry run **708**、実実行 **710** がSuccess。
**15,496,223 bytes（約14.8 MiB）、21 blobs、11 manifests**を回収した。
2時間の新規upload保護窓を維持し、日次予約ID 3が変更されず残ることも確認。
このGCは全projectを対象とするため、回収量を本番sandboxだけの削減量とは扱わない。
初回保持後のsandbox使用量は **1,160,293,492 bytes / 4,294,967,296 bytes**。
新ビルドが増えたため初回pilot時点の使用量との単純比較はしない。

### 公開workflow

全7repoでversionタグ、default branch HEAD照合、公開jobの直列化を反映した。
hitomi-pdf-batchのbatch/viewerの個別ビルド・テストと、各repo固有のテストを維持した。

| repo | 確認run |
|---|---|
| api-hub | [40628925210](https://github.com/ksera524/api-hub/actions/runs/40628925210) |
| hitomi | [40632271275](https://github.com/ksera524/hitomi/actions/runs/40632271275) |
| home-camera | [40632273780](https://github.com/ksera524/home-camera/actions/runs/40632273780) |
| hitomi-pdf-batch | [40632277507](https://github.com/ksera524/hitomi-pdf-batch/actions/runs/40632277507) |
| blog | [40632285574](https://github.com/ksera524/blog/actions/runs/40632285574) |
| cookLog | [40632288894](https://github.com/ksera524/cookLog/actions/runs/40632288894) |
| RSSHub-private | [40659226971](https://github.com/ksera524/RSSHub-private/actions/runs/40659226971) |

RSSHubの初回runはworkflowと既存CUSTOM_RSS.mdの書式チェックで停止したため、oxfmt 0.72.0で整形した。
workflowのYAMLデータが整形前後で一致すること、actionlint成功を確認して再実行した。

### 回帰検証

保持・切り戻し・OCI index・旧Job・多重起動・Lease初期化・GC設定・削除候補なしの省略を含む16テストが成功。
停止アラートのpromtoolテスト、全体の`automation/scripts/ci/validate.sh`も成功した。
全repoの変更workflowはactionlintで検証済み。

定期確認で削除候補がない場合はHarbor保持Jobを作らず終了するよう調整した。
状態確認・配布の昇格・GC予約の修復は5分ごとに継続し、保持履歴の無駄な増加を抑える。

18:25 JSTの定期Job `harbor-maintenance-29886325`では、全8アプリeligible、blocked=[]、
`delete_candidates=0`を確認して11秒で成功した。削除候補なしの回に保持executionを作らない動作も実環境で確認した。
vmalertの`HarborMaintenanceStalled`はhealth=ok、state=inactive、評価エラーなし。

### 最終確認（22:43 JST）

上表の全7repoの公開workflowが成功した。RSSHubも新しいdigest
`sha256:14cc59fc56bc0471cd67d0ed6591b9924cb049dec4af55e5c6b69dae72ee6532`
へ自動配布され、`retain-current`・`deploy`・`latest`が一致した。
直前正常版`sha256:0a04e9693bfe292d208a4fb9ace2a5e488641e74b23d5f6f00b4a964d800e883`
は`retain-previous`として保持された。Argo CDの全ApplicationはSynced / Healthy。

19:55 JSTのJob `harbor-maintenance-29886355`はhitomiのPod Readyまたは旧Pod終了待ちで
保護停止した。その後は自動復帰し、22:40 JSTのJob `harbor-maintenance-29886580`は
全8アプリeligible、blocked=[]、削除候補0で成功した。
この時点のsandbox使用量は **1,752,612,568 bytes / 4,294,967,296 bytes**。
保守コード最終変更のCI [40663600027](https://github.com/home-lab-ksera/home-lab-k8s/actions/runs/40663600027)も成功した。
