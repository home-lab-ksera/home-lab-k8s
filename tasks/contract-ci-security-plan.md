# 構成台帳とCI検証の詳細計画：公開経路の整合性とセキュリティ

作成日: 2026-11-08

状態: ローカル実装・静的検証済み。本番反映とGitHubのマージ制限設定は未実施。
[全体計画](cluster-reliability-improvement-plan.md)の項目1を具体化する。監視追加、ARC撤去、復元検証は本計画の対象外。

## 実装状況（2026-11-08）

- v1alpha2台帳とJSON Schema、共有renderer、CT-001〜CT-012、PR比較元との差分レポートを実装した。16公開面・12転送profile、52配備source・620リソースを照合した。
- Dockerの固定toolchainで `automation/scripts/ci/validate.sh` が成功。新規回帰テストは公開範囲・収集失敗・入力検証・秘密値を出さない診断・差分レポートを確認する。
- Gatewayのnamespace selectorとNature Remoの台帳注釈はmanifestに実装済み。実クラスタAPIには接続できず、同期・Accepted/ResolvedRefs・実疎通は未確認。
- GitHubのmainブランチ保護・rulesetは読み取り時点で未設定。CIの追加だけではマージ禁止を強制しない。
- 当初のA〜Dの独立PR案から、台帳形式とcheckerの同時移行を一つのレビュー単位に変更する。v1alpha1への互換読込みや `allowedRoutes: All` の移行例外は導入しない。Gatewayだけを旧設定へ戻すとCT-009が失敗するため、反映後に切り戻す場合は、この変更一式をGitでrevertして既存の検証へ戻す。
- 下記は着手時の設計・受け入れ条件を残したもの。現行の使用方法と制約は [docs/reference.md](../docs/reference.md) と [docs/access.md](../docs/access.md) を参照する。

## 1. 到達目標

台帳を「公開を許可する構成」、マニフェストを「実際に配備する構成」として照合する。
CIが次の変更を検出し、必要なレビューと修正を経てからGitOpsへ反映できるようにする。

- 意図しないNodePort/LoadBalancer、HTTP平文転送、未登録Routeの追加。
- 許可外Serviceへの転送、別namespace参照、リクエスト複製による送信先追加。
- GatewayへのRoute接続範囲の拡大、証明書参照やhostnameの不一致。
- Kustomize/Helmで最終的に追加された公開設定、検査対象の欠落。

静的検証の対象はGit管理された宣言とレンダリング結果。`internal`という名称や台帳の値だけでは通信を制限できない。
実際のInternet到達性、Tailnet ACL、ルーター/NAT、実証明書、クラスタへの直接変更は別の実測・制御が必要。
NetworkPolicy全体の再設計、RBAC全面改修、Admission Controller導入、アプリ認証方式の変更は含めない。

## 2. 現行コードから確認した課題

| 箇所 | 現状 | 改善の必要性 |
|---|---|---|
| `cluster-contract.yaml` | api-hubの `nodePort: 32001` が残る | 実ServiceはClusterIP。公開方式の食い違いを検出できていない |
| `contract-check.py` | access配下の注釈付きHTTPRouteだけを読む | 注釈削除、新しいディレクトリ、別種のRouteで検査を回避できる |
| backend検証 | 期待するService名が含まれるかを確認 | 正しいbackendに許可外backendを追加しても検出できない |
| parent検証 | sectionName中心で照合 | Gatewayのname/namespace、Route所有namespaceの変更を十分に検出しない |
| Gateway | 全listenerで `allowedRoutes.namespaces.from: All` | 他namespaceからRouteを接続できる範囲が広い |
| Nature Remo | Tailscale Ingressを使用 | HTTPRoute専用の台帳チェックでは対象外 |
| Harbor | coreとportalをパス別に参照 | 単一backendの台帳では実際の許可範囲を表現できない |
| render処理 | 既存の衝突検査は一部source形式をskipする | 外部Helm、multi-source、directory指定を一律に検査済みと扱えない |

根拠: [台帳](../manifests/contracts/home-lab/cluster-contract.yaml)、[checker](../automation/scripts/ci/contract-check.py)、[Gateway](../manifests/access/gateway/gateway.yaml)、[Nature Remo](../manifests/access/nature-remo/manifest.yaml)、[Harbor](../manifests/access/harbor/manifest.yaml)、[render検査](../automation/scripts/ci/policy-check.py)。
これらは静的な検査範囲・設定の指摘であり、実際の外部到達や侵害を確認したものではない。

## 3. 台帳の設計

### 正本と責務

- `cluster-contract.yaml`: クラスタ共通のIP・DNS・既存ネットワーク値と、公開に関係するServiceの許可方式。api-hubの古いNodePortを除去し、namespace/name/type/portを明示する。
- `access-surfaces.yaml`: 公開面ごとのRoute identity、hostname、許可parent、backend一覧、TLS、DNS配布先、許可する特殊なfilterを定義する。
- JSON Schemaを `automation/schemas/contracts/` に新設し、構造・型・enum・必須項目・未知フィールドを検証する。YAML重複キーも読み込み時に拒否する。
- semantic checkerは参照の存在と関係、公開の制約を扱う。IPやhostnameをPythonの定数へ複製しない。
- `consistency-check.py` にある同種の固定値チェックは段階的に移管する。既存GitOps登録検証、一般policy、kubeconformとは責務を分ける。

### 公開面の項目

| 項目 | 内容 |
|---|---|
| 公開意図 | `cluster` / `internal` / `external`。既存external設定の妥当性は棚卸しし、自動的に現状を承認しない |
| 経路種別 | `gateway-http` / `tailscale-ingress`。種別ごとにschemaを分ける |
| Route | apiGroup/kind/namespace/name。HTTPS用・redirect用を別identityで列挙 |
| hostname | Gateway経路は完全一致を基本とし、空hostnameや新しいwildcardを禁止 |
| parent | Gateway namespace/name/listener。HTTP用listenerはredirect専用として分離 |
| backend | Service namespace/name/portの許可リスト。全backendの登録と必要backendの存在を照合 |
| 転送条件 | Harbor等は重要なpath→backendの対応も記録。全YAMLを複製せず、機密経路や転送先の意味を持つ条件に限定 |
| TLS | Terminate、証明書Secret参照、Certificate/issuer参照、hostname適合条件 |
| DNS | CoreDNS/Split DNSへの登録と期待IP。API/DNSの既存台帳を参照 |
| filter | redirect、rewrite、header操作、mirror、extensionを列挙。送信先や認可に影響する変更を黙認しない |

例としてapi-hubは `sandbox/api-hub:3000` のClusterIP、内部hostname、指定GatewayのHTTPS listenerに限定する。
Harborは `harbor-core:80` と `harbor-portal:80` を登録し、`/api/`・`/service/`・`/v2/`等とportalの対応を検証する。
旧Harbor hostnameは台帳上internalの互換経路として維持し、`-external`という名前だけでInternet公開と判定しない。
Nature Remoは `ingressClassName: tailscale`、TLS hostname `nature-remo`、backend、tags、Funnel等の公開拡張annotationを別ルールで検証する。

schema変更はchecker・台帳・テストを同じPRで移行する。現行v1alpha1との互換性を維持できない変更はv1alpha2として明示し、移行後に旧形式へ黙ってフォールバックしない。

## 4. 配備対象を取りこぼさない収集

1. `bootstrap-root` が参照するKustomizationを起点にApplicationを取得し、子Application/ApplicationSetを追跡する。到達しないファイルを配備済みとみなさない。
2. 現在のsource形式を一覧化し、Kustomize、Helm、directory.include、multi-sourceのvalues参照、既存ApplicationSet generatorを処理する。valuesの優先順位、releaseName、destination namespace、patchを反映する。
3. `gitops-check.py`、`policy-check.py`と共有する収集・renderモジュールを作り、同じApplicationを何度もrenderしない。最初は対象資産に関係するsourceを完全に解決し、未解決sourceがある状態でクラスタ全体の公開検査を合格にしない。
4. リソースindexは `(apiGroup, kind, namespace, name)` とし、source Application・元path/chart・適用revisionを保持する。cluster-scoped kindへ誤ってdestination namespaceを補わない。
5. 同一identityの重複はエラー。multi-sourceの上書き等を利用している場合は意図と優先順位を明示し、偶然のlast-winsにしない。
6. 全配備対象からHTTPRoute/Ingress/Gateway/ReferenceGrant/公開Service/hostPort/hostNetworkを検査し、登録済みsurfaceと双方向照合する。未登録・重複・消失のいずれも検出する。
7. CRDのschema内の `NodePort` 等を実リソースと誤認しない。operator生成資産は親CRと採用controller設定から検証可能な部分を整理し、生成資産がrenderに存在するという前提を置かない。

NGINX GatewayのLoadBalancer、Tailscale operator生成資産、PGO生成Serviceなどは生成方式を個別に確認する。
CIが検証する宣言と、反映後に確認する生成資産をcoverage一覧で分ける。namespace全体を除外する逃げ道は作らない。
未知の公開用kindや未対応sourceは理由付きの検証失敗にする。対象外のCRは一覧化し、任意CRの全生成挙動まで保証したとは扱わない。

## 5. セキュリティ検証ルール

| ID | CIで拒否する変更 | 正常として扱う構成 |
|---|---|---|
| CT-001 | 未登録Route/Ingress、surface注釈の削除、無断のhostname追加 | identityとsurfaceが一対一で対応する既存経路 |
| CT-002 | internalサービスのNodePort/LoadBalancer化、未登録externalIPs | 台帳に登録したGateway/DNS等の必要なLoadBalancer |
| CT-003 | 未登録hostPort/hostNetwork、ExternalNameやselectorなしServiceによる迂回 | NetworkPolicy controller等、用途とidentityを限定した基盤例外 |
| CT-004 | 正規backendに追加した許可外宛先、誤port/namespace/group/kind | Service portに一致する登録backendのみ。weight=0でも無断宛先は拒否 |
| CT-005 | Gatewayの差替え、追加parent、sectionName省略による接続拡大 | 明示されたGatewayとlistenerのみ |
| CT-006 | HTTP listenerからbackendへ直接転送、HTTPへの降格、外部hostへのredirect | 同一hostnameのHTTPSへの301/308 redirect等、登録済み条件 |
| CT-007 | HTTPSのTLS欠落、未登録証明書参照、SAN不一致、未承認issuer | 対応Certificateが宣言され、hostnameを覆う参照 |
| CT-008 | 許可のない別namespace backend/証明書参照、過大なReferenceGrant | 宛先namespaceに存在する必要最小限のgrant |
| CT-009 | allowedRoutesをAllへ拡張、許可namespace/kindを追加 | listenerごとに許可namespaceとHTTPRouteを限定 |
| CT-010 | 未登録RequestMirror/ExtensionRef、外部宛先へ転送するrewrite | 登録済みfilterを指定した条件でのみ許可 |
| CT-011 | DNS登録漏れ・余分な登録・誤IP・重複hostname | 台帳の公開先と一致するCoreDNS/Split DNSの設定 |
| CT-012 | Tailscale Ingressのclass/backend/tags変更や未登録のFunnel有効化 | 現行のTailnet内公開意図に一致する経路 |

追加の判断規則:

- Route→Gatewayの別namespace接続はallowedRoutesで判定する。backend/証明書等の別namespace参照に使うReferenceGrantと混同しない。
- ReferenceGrantの `to.name` を省略した全Service許可は原則拒否する。API上grantのfromはRoute名単位に絞れないため、Route identityのCI照合を併用する。
- `*.qroksera.com` を内部の多階層hostnameに適合すると判定しない。証明書のhostname適合とGateway hostnameの照合を別々に実装する。
- cert-managerが生成するSecretをGitに置くことは要求しない。CertificateのsecretName/dnsNames/issuerを静的に照合し、実際の証明書期限・chainは実行時の確認事項にする。
- Gateway→backendのHTTPは現在の構成として扱い、今回一律にHTTPS化しない。BackendTLSPolicyがある対象は、その参照と検証条件の削除・緩和を検知する。
- 基盤例外はidentity、許可する項目、理由、管理元を明示する。現存するという理由だけでは追加せず、不要になった例外も検出する。

## 6. Gateway接続範囲の実設定強化

CIでの予防に加え、既存 `allowedRoutes: All` を限定する変更を独立PRにする。

1. 各HTTPS listenerを、そのsurfaceのRouteが存在するnamespaceに限定する。
2. 共有HTTP listenerは登録済みredirect Routeのnamespace集合だけを許可する。
3. `allowedRoutes.kinds` はHTTPRouteを明示する。namespace selectorには `kubernetes.io/metadata.name` を使い、専用の自由変更可能なラベルを増やさない。
4. 既存の全Routeがselector条件を満たすことをCIで検証した後に同期する。
5. 反映後にAccepted/ResolvedRefs、HTTPS到達、HTTP redirectを確認する。代表経路はapi-hub、Harbor core/portal、ArgoCD、Grafana、RSSHub、viewer、RustFS。

同じnamespace内の別Routeを防ぐ境界にはならないため、CIのidentity検証も維持する。
Namespace/Routeを書き換えられる権限や直接kubectlでの変更をCIだけで防ぐとは扱わない。
失敗時はこのPRのGateway変更だけをrevertする。検証を恒久的に無効化して切り戻さない。

## 7. CI自体の安全性とレビュー

- 通常の `pull_request` と `contents: read` で実行し、本番kubeconfig、クラスタ接続、Secret取得、書込み権限を不要にする。
- untrusted PRコードを権限付き `pull_request_target` で実行しない。checkoutの認証情報を不要に永続化しない。
- renderは引数配列で起動し、shell文字列へ入力を埋め込まない。repo外へのpath/symlink脱出、未承認plugin/exec、無制限の再帰を拒否し、timeoutを設定する。
- 外部chart/toolは採用版と取得元を固定し、既存vendored chartを優先する。公開物の取得に必要なnetworkと実クラスタ接続を分離し、取得失敗をskip成功にしない。
- ログとartifactにはSecret.data/stringData、Helm展開したSecret、ConfigMap内の資格情報を含めない。検証に必要なidentityと非機密フィールドの差分だけを出力する。
- 台帳とmanifestを同時に広げれば一致検査だけは通るため、base/headから「公開拡大の差分」を出す。internal→external、新hostname、NodePort、grant拡大、例外追加、検証器変更を明示する。
- GitHub側で検証jobがrequired checkか、管理者のbypass、実行主体制約、review運用を読み取りで確認する。CODEOWNERSだけでは必須承認にならず、単独運用で自己承認できない制約も考慮する。
- branch/ruleset設定は存在を仮定しない。利用可能な保護を設定できた状態と、CI導入のみの状態を分けて完了報告する。レビュー設定の変更は独立した変更として提示する。

## 8. テスト設計

実ファイルを書き換えない一時fixtureで、1条件だけ変更したときに対応ルールが失敗することを検証する。

| 分類 | 代表ケース |
|---|---|
| schema | 未知キー、誤型、重複キー、旧形式、重複surface、空hostname |
| 収集 | 未登録Application、nested Application、directory.include、multi-source、namespace既定値、外部Helm、patch後だけに現れる公開設定 |
| 検査回避 | 注釈削除、access外へのRoute移動、新規Ingress、未対応公開kind、未解決source、chart取得失敗 |
| backend | 正規＋不正backend、別namespace、Service portとtargetPortの取り違え、mirror宛先、ExternalName |
| TLS/parent | 別Gateway、追加parent、HTTP直結、TLS削除、wrong issuer、wildcard境界、過大grant |
| 公開範囲 | NodePort復活、未登録LoadBalancer/externalIPs、hostPort、allowedRoutes All、Tailscale公開拡大 |
| 正常系 | Harbor複数backend/パス分岐、redirect-only、RustFSの条件分岐、旧Harbor hostname、Nature Remo Ingress、operator生成資産の登録 |
| CI安全性 | path脱出、plugin指定、timeout、Secretを含む入力でもログに値が出ない、公開拡大diff |

既存 `automation/tests/ci/` のunittestに追加し、`make validate` から実行する。採用toolchainはmise/Dockerの既存環境を使う。
現在の素のpython3にはPyYAMLがないため、計画確認ではcheckerを実行していない。実装時は既定toolchainでbaselineを取得する。
本番全体の検証で既存不整合が見つかった場合は、項目ごとに修正または根拠付き例外を判断し、一括除外しない。

## 9. PR分割と受け入れ条件

| PR | 内容 | 合格条件 |
|---|---|---|
| A | source/公開経路の棚卸し、schema・台帳の拡張、api-hubの旧NodePort修正 | Nature RemoとHarborを含む現行経路が台帳で表現でき、配備リソースのrender差分がない |
| B | 収集/index共通化、整合性検証、現行checkerからの移行 | 全sourceのcoverageが明示され、正常系と不整合の変異テストが成功 |
| C | CTルールと公開拡大diff、CI統合・安全性確認 | 意図しない公開や参照拡大がCIで失敗。検査不能・例外漏れが成功扱いにならない |
| D | Gateway allowedRoutesの限定、CT-009の厳格化、運用文書更新 | 既存Routeが正常、許可namespaceが必要最小限、切り戻しが可能 |

PR Aでは後続checker用の台帳とschemaのテストを同時に追加し、移行期間の読み取り互換を明示する。
PR C時点のallowedRoutes Allは現行値を限定列挙した移行例外として可視化し、PR Dで例外とAllを同時に除去する。
各PRは既存CIが通る独立した状態にし、全変更を1回の巨大なrender刷新へまとめない。

目安はA: 0.5〜1日、B: 2〜3日、C: 1〜2日、D: 0.5〜1日。合計4〜7作業日。
外部Helm/operator生成資産の再現方法によりBを再見積りする。required check等のGitHub設定確認は別の完了項目として追跡する。

## 10. 最終完了条件

- [ ] 全配備sourceと全公開面の検査範囲が一覧化され、未解決項目が残っていない。
- [x] api-hubのNodePort記述がなく、再追加を回帰テストで検知する。
- [x] 未注釈Route、許可外backend、HTTP直結、TLS欠落、過大grant、公開範囲拡大を検知する。
- [x] Nature RemoとHelm/operator由来の公開設定を黙って検査対象外にしていない。
- [ ] GatewayのAllを除去し、既存の各経路の正常性を確認した。
- [x] 既存 `make validate` 相当と新規変異テストが成功し、秘密値を診断へ出さないテストが通る。
- [x] required check等の実設定と、CIでは保証しない範囲を明記した。
- [x] docs/access.md、docs/reference.md、必要な台帳説明が最終実装と一致する。

## 11. 仕様の確認先

- [Gateway API Security](https://gateway-api.sigs.k8s.io/docs/concepts/security/): Routeの接続許可とnamespace間の信頼境界。
- [ReferenceGrant](https://gateway-api.sigs.k8s.io/reference/api-types/referencegrant/): 別namespace参照の許可。Route→Gateway接続との区別。
- [Gateway API TLS](https://gateway-api.sigs.k8s.io/guides/user-guides/tls/): listener、証明書参照、backend TLSの区別。
- [GitHub: pull_request_targetの安全な利用](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target): PRコードと権限付き実行を混在させない。

仕様は実装時に採用CRD/controller/toolchain版へ照合する。この文書内の新規schema、ルールID、共有モジュールは設計案であり未実装。
