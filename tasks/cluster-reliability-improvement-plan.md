# クラスタ改善計画：復元検証・疎通監視・ARC整理・構成整合性

作成日: 2026-11-08

状態: 項目1のみローカル実装・静的検証済み。[詳細と未完了条件](contract-ci-security-plan.md)。他の項目、実クラスタへの反映、GitHub設定変更、復元試験は未実施。
現状の根拠はローカルの宣言・スクリプトであり、本番の適用状態は着手時に確認する。
以下は着手前の調査と全体計画を記録したもの。

## 1. 対象と成果

| 項目 | 成果 |
|---|---|
| 構成整合性 | 台帳と配備対象のService・Route・DNSの不一致をCIで検出する |
| 疎通監視 | DNS・TLS・Gateway・主要アプリの応答とHTTPRoute異常を検知する |
| ARC整理 | GitHub-hosted＋Tailscale OIDCへの移行を確認し、未使用のARC資産を撤去する |
| 復元検証 | PostgreSQLとetcdの隔離復元を再実行可能にし、定期検証の証跡を残す |

OCI移行、容量最適化、DBロール分離、別媒体バックアップ、クラスタ外監視基盤の新設は対象外。
既存の共用app資格情報とGitOps運用を維持する。上記4項目のための一時リソース見積りは実施する。
今回の計画作成では既存ファイルや稼働環境を変更しない。

## 2. 確認済みの差分

- [contract-check.py](../automation/scripts/ci/contract-check.py) は [consistency-check.sh](../automation/scripts/ci/consistency-check.sh) 経由で既存CIに組み込まれている。hostname・listener・backend名・DNSは照合するが、Service種別・backend port・参照先namespaceの厳密な検証は不足している。
- [cluster-contract.yaml](../manifests/contracts/home-lab/cluster-contract.yaml) のapi-hubには `nodePort: 32001` が残るが、[実Service](../manifests/apps/api-hub/manifest.yaml) は `ClusterIP`。
- [HTTPRouteメトリクス](../manifests/platform/observability/kube-state-metrics.yaml) は `conditions[0].status` を参照する。Accepted・ResolvedRefsを区別する情報が不足し、[alert-rules.yaml](../manifests/platform/observability/config/alert-rules.yaml) にHTTPRoute専用ルールはない。
- [ARC定義](../manifests/platform/ci-cd/github-actions/kustomization.yaml) はcontrollerと共通ScaleSetを管理している。確認済みのアプリ公開workflowはhosted runnerへ移行済みだが、組織全体の未使用確認は未実施。
- [復元例](../automation/examples/postgresql/restore-test.yaml) は旧Harbor用の名前・roleが残る。現行DBはregistry/api_hub/hitomi/media、camera履歴DBは存在する場合に保全する。
- [更新前チェック](../automation/scripts/upgrade/upgrade-precheck.sh) はetcd snapshotを取得するが、この取得成功だけでは復元可能性を保証しない。

## 3. 実施順序とPR分割

| 順序 | PR単位 | 依存・開始条件 | 完了条件 |
|---|---|---|---|
| 0 | 読み取り調査・基準記録 | なし | 適用状態、全workflow参照、backup、隔離試験の容量と接続先を記録 |
| 1 | 台帳・検証の修正 | 調査完了 | 正常構成が通り、意図的な不整合がCIで失敗 |
| 2 | 疎通監視・HTTPRoute監視 | PR 1の対象台帳確定 | 試験対象で発火・復旧・欠損検知を確認 |
| 3a | ARC停止準備・ScaleSet撤去 | 全利用元移行、実行中/待機ジョブなし | controllerを残した状態でRunner関連の正常終了・削除を確認 |
| 3b | ARC controller・専用資産撤去 | 3a完了、移行後の公開成功を確認 | 孤立リソースなし、公開とImage Updaterが正常 |
| 4a | PostgreSQL隔離復元 | backup識別、容量、隔離設計が確定 | 全対象DBの検証成功、時間・復元点を記録 |
| 4b | etcd隔離復元・定期実行 | 使用バージョンと隔離環境が確定 | 隔離APIで検証成功、定期実行と失敗/未実行検知が成立 |

PR 4a/4bの準備はPR 1以降に進められる。ARC撤去による空き容量が必要な場合だけ3b完了を待つ。
実装PRのマージと、本番同期・運用検証の完了を別々に記録する。

## 4. 構成台帳と実マニフェストの照合

この項目の実装順序・セキュリティ検証・受け入れ条件は[詳細計画](contract-ci-security-plan.md)を参照する。

### 変更内容

1. `cluster-contract.yaml` のapi-hubを現行のnamespace/name/type/portに合わせ、不要なNodePort項目を廃止する。
2. `access-surfaces.yaml` に許可するbackendのnamespace/name/Service portを記述する。Harbor等の複数backend、redirect-onlyのルールを表現し、単一backendを強制しない。
3. 既存checkerを拡張し、ArgoCDの登録先を辿ったKustomize/Helm展開結果を `(kind, namespace, name)` で照合する。未登録YAMLやchart内のサンプルを配備済みと扱わない。既存のGitOps検証・render処理を可能な範囲で共用する。
4. Service種別とport、Routeのbackend参照とparent Gateway/listener、DNSの宛先IPを検証する。HTTPRouteのportはService portと比較し、PodのtargetPortと混同しない。
5. 別namespace参照のReferenceGrant、証明書参照などは対象経路に必要なものを検証する。照合不能なHelm生成リソースは明示的に報告し、黙って合格させない。
6. `consistency-check.py` の固定値チェックとの責務を整理する。台帳から検証用の期待値を取得し、同じIPやhostnameを複数のPython定数へ増殖させない。

### テスト・完了条件

- `automation/tests/ci/` に正常系と変異テストを追加する。NodePort復活、backend port/namespace誤り、listener不一致、DNS誤り、未登録・欠落Serviceを検出する。
- redirect-only、複数backend、namespace省略時の既定解決は正常例として確認する。
- エラーに対象ファイル/リソース、期待値、実値を表示する。既存の `make validate` に統合し、本番への接続を要求しない。
- 台帳修正がServiceや公開経路の意図しない変更を含まないことをrender差分で確認する。

## 5. 主要URL・HTTPRoute監視

### 実装方針

- `observability` に小規模なblackbox exporterを配置し、既存VictoriaMetrics/vmalert/Grafanaへ接続する。imageを固定し、requests/limitsと必要な通信だけを設定する。
- 初期対象は内部URLのapi-hub、Harbor、ArgoCD、Grafana、viewer、RSSHub。正確なpath・期待status・安定した本文条件・redirect方針は事前調査で確定する。RSS取得や画像push等、副作用・外部負荷のある操作は定期probeにしない。
- probe対象のhostnameはaccess台帳から参照し、path等の監視固有設定だけを追加する。Gatewayを通るHTTPSを使用し、証明書検証を省略しない。
- CoreDNSに加えてSplit DNSのUDP/TCP 53と期待するAレコードを検査する。クラスタ内probeからはTailnet端末の経路・ACLまでは保証できないため、反映時に実端末でもDNS/HTTPSを確認する。
- HTTPRouteはparentのname/namespace/controllerとcondition typeを識別する。配列位置への依存を除き、Accepted/ResolvedRefsのFalse・Unknown・欠損を検知する。採用中kube-state-metricsの表現能力を実メトリクスで確認し、必要な情報を追加する。
- 台帳上の期待対象と突き合わせ、Route削除・condition欠落・probe系列消失を正常と扱わない。古いobservedGenerationの扱いも確認する。

### 初期運用と検証

- 初期案は60秒間隔、連続5分の失敗で通知。証明書期限はwarning 14日/critical 7日を目安とし、既存Certificate通知との重複を調整する。
- `promtool test rules` で正常・異常・Unknown・欠損・複数parent・復旧を検証する。condition順序の変更もfixtureで検証する。
- exporter設定を採用imageで検証し、専用の試験URL/RouteでDNS誤り、TLS不一致、誤ったbackend、本文異常を再現する。本番アプリを意図的に停止しない。
- 本番では正常値と発火/復旧経路を確認し、7日間の通知量を評価する。通知経路の試験は専用receiver等で完結させ、通常のSlackへ試験通知を無断送信しない。
- 切り戻しは新規probe/ruleのGit revert。既存のPod・業務キュー監視は維持する。

## 6. CI移行後のARC整理

### 撤去判定

1. ローカル9リポジトリに加え、組織のworkflow、reusable workflow、workflow_dispatch、runner groupの利用先を確認する。動的runs-on指定も対象とする。
2. 全利用元の移行commitとhosted＋OIDCでの公開成功を記録する。7日間の観測を目安とするが、低頻度workflowは未実行だから未使用とは判断せず、参照と実行経路を個別確認する。
3. 撤去直前にGitHubのqueued/in-progressジョブと、クラスタのRunner/Listener状態を照合する。不明な利用元が残れば該当資産を保持する。

### 段階的な撤去

- 3aでは利用元の新規投入をなくした後にScaleSetのApplicationSet要素を削除する。ArgoCDのcascade/finalizerを確認し、ARC controllerは子リソースの削除完了まで残す。
- 3bでcontroller Application、version ConfigMap、専用ServiceAccount/RBAC、ExternalSecret、NetworkPolicy、不要namespaceを参照確認後に撤去する。CRDは他の利用がないことを確認した最後の段階で扱う。finalizerの強制削除を通常手順にしない。
- `platform` Applicationは現在ARCのディレクトリを参照するため、削除・改名の影響をbootstrap登録、AppProject、sync wave図まで確認する。
- `harbor-ci-auth` 等のクラスタ内Secretと、OIDC経由の公開処理が使う外部資格情報を区別する。名前や元のESC項目が共通でも、残る利用者があれば元の資格情報は削除しない。
- `version-audit.py`、Renovate、policy/consistencyチェック、runnerテスト、説明文を更新する。image digest反映の既存テストをrunnerテストと一緒に失わないよう分離する。

### 完了・切り戻し

- 各利用元の公開成功、Harborへの到達とpush、Image Updaterのdigest反映を確認する。失敗時は失敗工程を切り分け、公開済みimageを無条件に作り直さない。
- ArgoCDのMissing/OutOfSync、孤立したRunner、不要なSecret/RBACが残っていないことを確認する。
- 切り戻し資料にcontroller→ScaleSet→利用元の復旧順序と必要な資格情報の参照先を残す。外部資格情報の削除は利用終了の最終確認後に分ける。

## 7. 復元手順の定期検証

### PostgreSQL

1. `pgBackRest info`、成功時刻、backup識別子、復元対象時点、対象DB、必要な空き容量を記録する。稼働中DBの現在の行数を過去backupの正解として比較しない。
2. 既存restore例を現行構成へ更新し、実行ごとに一意なPostgresClusterと新規PVCへ復元する。同namespaceでのcloneが必要な場合も、Service・ラベル・資格情報・通信許可を本番から分離する。
3. 採用PGO/pgBackRestのCRDと動作を確認し、本番repoへの書込み・期限切れ削除を試験が起こさない構成にする。本番PVCの再利用や同じdata directoryへの復元を拒否する。
4. registry/api_hub/hitomi/media、および存在するcamera履歴について、DB・schema・主要テーブル・migration世代・所有権・代表的な参照を検証する。backupの復元点に対応する期待値を用意する。
5. TLS/認証とアプリ相当の読み取り接続を確認する。通知worker、録画、S3 upload、Harbor本体等の外部副作用を持つ処理は試験先に起動しない。
6. source backup ID、復元された時点、開始/終了時刻、所要時間、検証結果を保存する。DBの復元だけでS3 objectとの整合やクラスタ全体復旧が成功したとは判定しない。
7. 初回手動成功後、月1回の自動検証を有効にする。多重実行を防止し、timeoutを設け、成功時は試験所有の資産だけを削除する。失敗時は上限期間・容量を定めて診断用資産を保持する。

### etcd

1. 実クラスタのKubernetes/etcdバージョンとsnapshotを確認し、対応するetcdutlで整合性・revision・件数を記録する。PKI、暗号化設定、static Pod manifest等の復旧に必要な構成も棚卸しする。
2. 本番のAPI/etcd peer、worker、GitOps、外部通知先へ到達できない隔離環境に別data directoryで復元する。Secretを含むsnapshotをCI artifactやGitへ保存しない。
3. 最初は復元etcdの読み取り検証を行い、隔離kube-apiserverで代表的なAPI objectを確認する。scheduler/controller-managerや復元された業務Podを本番へ接続させない。
4. 採用バージョンでrevision bump/mark-compacted等の必要性と対応を確認し、実復旧用の手順に記録する。実クラスタのetcdを置換する試験は行わない。
5. 初回とKubernetes/etcd更新時に隔離API確認まで実施し、月1回のsnapshot復元検証を定期化する。実行先はホスト側の隔離VM/コンテナを第一候補に、容量と通信遮断を調査して確定する。

### 定期検証の監視・完了条件

- PostgreSQLとetcdの実行をずらし、最大同時実行1、開始前の空き容量チェックと実行期限を設ける。共有NFSへの負荷で本番に影響が出たら停止・延期する。
- 最終試行・最終成功・所要時間・失敗段階を既存監視へ公開する。初期値は月次、成功が40日以上ない場合と実行失敗を検知する。系列欠損も検知する。
- 初回測定で復旧時間・復元可能時点を提示し、運用目標を確定する。snapshot/backupの経過時間と実際のデータ損失幅を混同しない。
- 定期実行が少なくとも1回成功し、失敗・未実行ケースの検知、資産の後片付けまで確認して完了とする。設定投入だけでは完了にしない。
- 切り戻しは検証スケジュールの停止と試験専用資産の撤去。本番backup設定・本番PV/PVCには触れない。

## 8. 全体の受け入れ・進捗管理

- [ ] 調査記録に対象cluster context、Git revision、backup識別子、全CI利用元、未確認事項を記載した。
- [ ] PR 1: 正常/不整合の回帰テストと `make validate` が成功した。
- [ ] PR 2: 試験対象の異常/復旧/欠損を検知し、本番正常値と7日間の運用を確認した。
- [ ] PR 3a/3b: 全利用元の移行と段階削除、公開・digest更新の継続を確認した。
- [ ] PR 4a/4b: 初回隔離復元、定期実行、失敗検知、後片付けの証跡を保存した。
- [ ] 関連するdocs、構成図、運用手順を最終構成に合わせた。

実装と初回検証は1〜2週間程度を仮見積りとする。隔離環境の準備と全利用元確認で再見積りし、月次実行の受け入れ完了までは別に観測期間を設ける。
この計画に挙げた新規スクリプト・設定・テストは未実装であり、既存のコマンドとして扱わない。

## 9. 実装時の仕様確認先

- [PGO: Disaster Recovery and Cloning](https://access.crunchydata.com/documentation/postgres-operator/latest/tutorials/backups-disaster-recovery/disaster-recovery): 既存clusterのrepoを指定した復元。実装では同梱CRDと採用版に照合する。
- [etcd: Disaster recovery](https://etcd.io/docs/v3.7/op-guide/recovery/): snapshot復元とrevisionの扱い。参照先は3.7であり、実クラスタのバージョン確認後に対応版を使用する。
- [blackbox exporter設定](https://github.com/prometheus/blackbox_exporter/blob/master/CONFIGURATION.md): DNS、HTTP、TLS、本文検証。採用imageの版に合わせて設定を検証する。
