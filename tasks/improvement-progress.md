# 進行状況管理票：全体改善計画

対象: [全体改善計画](improvement-plan.md)

最終更新: 2026-11-14

状態の値: `未着手` / `進行中` / `確認待ち`（反映済みで完了条件の確認前） / `完了` / `保留`（判断または前提待ち） / `取り下げ`

## 集計

| Phase | 項目数 | 完了 | 進行中・確認待ち | 未着手 | 保留 |
|---|---:|---:|---:|---:|---:|
| A 重要 Pod の保護とホスト | 6 | 5 | 1 | 0 | 0 |
| B 依存更新と CI | 6 | 2 | 2 | 1 | 1 |
| C 監視範囲とディスク | 6 | 5 | 0 | 0 | 1 |
| D セキュリティ設定と調査 | 6 | 5 | 1 | 0 | 0 |
| E アプリ整理 | 3 | 0 | 0 | 1 | 2 |
| F 長期 | 2 | 0 | 0 | 0 | 2 |
| 合計 | 29 | 17 | 4 | 2 | 6 |

## 項目

### Phase A

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| A-1 | PriorityClass の追加と付与 | GitOps | 完了 | #158 | 2026-11-14 | Gateway control plane、Split DNS、PostgreSQL、Harbor core / registry に付与。Gateway data plane と Tailscale は対象外（計画 A-1）。反映順の不備で DB が約4分停止（下の「障害記録」） |
| A-2 | BestEffort の Pod に requests | GitOps | 完了 | #161 | 2026-11-14 | ArgoCD 7、Gateway data plane、Tailscale operator、provisioner 2。Gateway は chart のキー誤り（`nginx.resources`）を修正。残る BestEffort は kube-proxy と pgBackRest repo-host |
| A-3 | control-plane の requests 削減、CoreDNS の配置 | GitOps | 完了 | #161 | 2026-11-14 | CPU requests 93% → 75%。CoreDNS は `rollout restart` で 2 ノードへ分散（Git 変更なし。kubeadm 管理） |
| A-4 | NFS export を VM サブネットに限定 | 管理者 | 完了 | ホスト作業 | 2026-11-14 | export は `nfs-share 192.168.122.0/24` の 1 行。作業中に export が 0 件になり NFS 依存 Pod が停止（「障害記録」） |
| A-5 | ホスト再起動 | 管理者 | 完了 | ホスト作業 | 2026-11-14 | kernel 7.0.0-43。両 VM は autostart で復帰 |
| A-6 | kubectl 1.36、不要サービス停止 | 管理者 | 進行中 | ホスト作業 | | kubectl 1.36.0 と fwupd 停止は完了。docker（未使用）と snapd は稼働中 |

### Phase B

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| B-1 | Renovate App の対象確認と Dashboard | 管理者 | 未着手 | | | GitHub の App 設定（Organization → Installed GitHub Apps → Renovate）で 8 リポジトリを対象に入れる |
| B-2 | 既知の遅れを解消 | 各アプリ | 進行中 | 下表 | | Node / nginx / go は解消。api-hub・hitomi・home-camera のテスト workflow の `actions/checkout@v4` が残る |
| B-3 | `rust-toolchain.toml` の統一 | 各アプリ | 完了 | 下表 | 2026-11-14 | 4 リポジトリとも 1.99.1。テストは `@stable` をやめこの版で実行 |
| B-4 | 共通 Rust 検証 workflow | GitOps + 各アプリ | 完了 | #159、#164、下表 | 2026-11-14 | fmt・clippy・cargo-audit（報告のみ）。指摘は job summary と job ログに出る |
| B-5 | audit のゲート化判断 | 判断 | 保留 | 判断 4 | | B-6 で hitomi の指摘が解消してから決める |
| B-6 | cargo-audit / clippy の指摘解消（B-4 の結果で追加） | 各アプリ | 進行中 | 下表 | | hitomi が残る。rustls 0.21 → 0.23 と tokio-rustls の移行が必要で、`dangerous_configuration` を使う `src/util/http_client.rs` の書き換えを伴う |

リポジトリ別。対象外は `-`。

| リポジトリ | B-1 Dashboard | B-2 遅れ解消 | B-3 toolchain | B-4 共通検証 | B-6 指摘解消 |
|---|---|---|---|---|---|
| api-hub | 未 | checkout が残る | 済（#19） | 済（#19） | #20（rustls 系の更新、clippy 7 件、fmt）。CI 通過後にマージ |
| hitomi | 未 | checkout が残る | 済（#35、1.88.0 → 1.99.1） | 済（#35） | 未。脆弱性 8 件（rustls 0.21 系）、clippy 6 件 |
| home-camera | 未 | checkout が残る | 済（#15） | 済（#15） | 済（#16、reqwest 0.12） |
| hitomi-pdf-batch | 未 | 済（#15） | 済（#14） | 済（#14） | #16（reqwest 0.12、quick-xml 0.41）。CI 通過後にマージ |
| nature-remo | 未 | 済（#4、`go 1.26`） | - | - | - |
| blog | 未 | 済（#5、Node 26・nginx 1.30） | - | - | - |
| cookLog | 未 | 済（#6、Node 26・nginx 1.30）。`Dockerfile.slack` の Node 22 は Renovate に任せる | - | - | - |
| RSSHub-private | 未 | - | - | - | - |

cargo-audit に残る指摘（対処不要と判断したもの）: `libsqlite3-sys` と `rsa` は sqlx の未使用 backend が lockfile に載るだけでビルドに含まれない。
`rand` の unsound 警告は推移的依存。

### Phase C

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| C-1 | Job 失敗アラート | GitOps | 完了 | #162 | 2026-11-14 | `KubernetesCronJobFailed`。NFS 停止中に失敗した Job 2 件で実際に発火し、解消した |
| C-2 | ホストの node_exporter とアラート | 管理者 + GitOps | 完了 | #162 | 2026-11-14 | `Host*` 5 件。VictoriaMetrics でホストの MemAvailable を取得できることを確認 |
| C-3 | 外形の疎通監視 | GitOps | 完了 | #166 | 2026-11-14 | Alloy 内蔵の blackbox exporter。8 URL と Split DNS が `probe_success=1`。Gateway を止める発火試験は未実施（ルール試験のみ） |
| C-4 | etcd defrag、control-plane のログ保持 | VM 内作業 | 完了 | #160 | 2026-11-14 | etcd DB 206MB → 18MB。journal は 500MB 上限（control-plane 3.9GB → 0.45GB）。監査ログ 1.1GB は設定どおり（D-4） |
| C-5 | worker2 の image GC 閾値 | VM 内作業 + Terraform | 完了 | #160 | 2026-11-14 | 70% / 60%。ルートディスク 72% → 43% |
| C-6 | `oci-always-free` Job と PVC の扱い | 判断 | 保留 | 判断 1 | | OCI 関連は当面扱わない |

### Phase D

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| D-1 | sandbox 4 Pod の securityContext | GitOps + アプリ | 確認待ち | #163、#167、#168、blog#5、cookLog#6、hitomi-pdf-batch#15 | | blog / cooklog は uid 101・読み取り専用 rootfs で稼働を確認。viewer は uid 65532。hitomi-monitor は次回の定期実行で確認する。Selenium sidecar は対象外のため `sandbox` は baseline のまま |
| D-2 | 監視 4 Pod の securityContext | GitOps | 完了 | #165 | 2026-11-14 | VictoriaMetrics は NFS 上のデータが root 所有のため root のまま権限だけ落とした。`observability` は baseline のまま |
| D-3 | 3 namespace に PSA ラベル | GitOps | 完了 | #160 | 2026-11-14 | local-path-storage privileged、nfs-storage baseline、postgres-operator restricted |
| D-4 | kube-apiserver メモリの調査 | 調査 | 完了 | 下の「調査結果」 | 2026-11-14 | 対処は判断 6・7 |
| D-5 | 高頻度 CronJob のコスト測定 | 調査 | 完了 | 下の「調査結果」 | 2026-11-14 | 対処不要 |
| D-6 | ドキュメントの修正 | GitOps | 完了 | #160 | 2026-11-14 | |

### Phase E

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| E-1 | 巨大ファイルの分割 | 各アプリ | 未着手 | | | 3 リポジトリ 4 ファイル。clippy が通る状態になったので着手できる |
| E-2 | `failures.rs` の重複確認 | 調査 | 保留 | 判断 8 | | 調査済み。公開関数 `list` / `history` / `act` が同名・同シグネチャで、同一行は 152 行（hitomi 側 357 行の約 4 割） |
| E-3 | 名前と役割のずれ、namespace 改名 | 判断 | 保留 | | | |

### Phase F

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| F-1 | OCI 継続か RAM 増設か | 判断 | 保留 | 判断 2 | | OCI は当面扱わない。RAM 増設は今はできない |
| F-2 | OCI 計画 Phase 0 | 調査 | 保留 | | | |

## 残作業（次に行うこと）

| 順 | 内容 | 担当 |
|---|---|---|
| 1 | api-hub#20、hitomi-pdf-batch#16 を CI 通過後にマージ | - |
| 2 | hitomi-monitor の次回定期実行が成功することを確認（D-1） | - |
| 3 | Renovate App の対象リポジトリ設定（B-1） | 管理者 |
| 4 | ホストの docker / snapd 停止（A-6）: `sudo systemctl disable --now docker docker.socket`。snapd はデスクトップを使わない場合のみ | 管理者 |
| 5 | hitomi の rustls 0.23 移行（B-6） | - |
| 6 | 判断 4・6・7・8 | 判断 |
| 7 | 巨大ファイルの分割（E-1） | - |

## 調査結果

### D-4 kube-apiserver（1.2GiB、150m）

- 監査ポリシーは全リクエストを `Metadata` で記録している。監査ログは約 107MB / 時で、100MB × 10 世代の上限により保持は約 10 時間。
- 直近 22 分（29,200 件）の内訳: Alloy の `get pods` と Pod ログ取得が 24%、ESO の Secret の get / list が 12%、各 controller の lease 更新が約 15%、匿名の health 確認が 3%。
- Alloy は `loki.source.kubernetes` で、全 Pod のログを API サーバー経由で取得している（毎秒約 4 リクエスト）。ログの流量はすべて API サーバーを通る。
- watch は 742 本。CRD は 80。メモリの内訳までは切り分けていない。

対処の候補（判断 6・7）:

1. 監査ポリシーで、ServiceAccount と control-plane コンポーネントの読み取り（get / list / watch）、lease、health 確認を `None` にする。ログ量は 6 割以上減り、保持は 1 日以上に延びる見込み。反映には API サーバーの再起動（1 分程度の API 停止）が要る。
2. Alloy を DaemonSet にしてノード上のログファイルを読む。API サーバーの負荷は減るが、Pod が 1 つ増え、hostPath を使うため `observability` の PSA を緩める必要がある。

### D-5 高頻度 CronJob

harbor-maintenance（10 分毎）、rustfs-scanner-monitor（15 分毎）、postgresql-credentials（30 分毎）、media-reconcile（毎時）で、
Pod 生成は 1 時間に 13 回、各 4〜9 秒で終わる。クラスタ全体の CPU 実使用（約 1.1 コア）に対して小さく、間引きや常駐化はしない。

## 障害記録（2026-11-14 の作業中）

| 時刻（JST） | 事象 | 原因 | 対処 |
|---|---|---|---|
| 00:51〜01:05 ごろ | NFS を使う Pod（RustFS、Harbor、VictoriaMetrics、home-camera など）が起動できず、Harbor 停止でアプリが ImagePull に失敗 | A-4 の作業で `/etc/exports` の行がコメントのままになり、ホスト再起動後に export が 0 件になった | 行を有効にして `exportfs -ra`。全 Pod が復帰 |
| 01:33〜01:37 ごろ | shared-postgresql の Pod が作成できず DB が停止 | #158 で PriorityClass と、それを参照する PostgresCluster を同じ PR に入れた。ArgoCD が `core`（PriorityClass 本体）より先に shared-postgresql を同期した | `core` を refresh して PriorityClass を作成。DB は自動で復帰 |

今後、クラスタ全体のリソース（PriorityClass、StorageClass、CRD など）を新設するときは、定義だけを先にマージして反映を確認し、参照する側は次の PR に分ける。

## 判断記録

| No | 判断事項 | 選択肢 | 決定 | 決定日 |
|---|---|---|---|---|
| 1 | `oci-always-free` の失敗 Job と PVC | 削除 / Git 管理化して再実行 / 現状維持 | 現状維持（OCI 関連は当面扱わない） | 2026-11-14 |
| 2 | 長期の方向 | OCI 計画を続ける / RAM 増設を先に検討 / 両方見送り | OCI 関連は当面扱わない。RAM 増設は今はできない | 2026-11-14 |
| 3 | ホストの sudo が必要な作業の進め方 | 手順書を用意して管理者が実行 / 他の方法 | 手順を提示し管理者が実行 | 2026-11-14 |
| 4 | cargo-audit のゲート化（B-5） | 報告のみ / 失敗で publish を止める | 未決 | |
| 5 | CoreDNS の配置（A-3） | 2 replica を分散 / 1 replica | 2 replica を分散 | 2026-11-14 |
| 6 | 監査ポリシーの絞り込み（D-4） | 現状維持 / 読み取りと lease を除外 | 未決 | |
| 7 | Alloy のログ収集方式（D-4） | 現状維持（API 経由） / DaemonSet でファイルを読む | 未決 | |
| 8 | `failures.rs` の共通化（E-2） | 現状維持 / 共通 crate に切り出す | 未決 | |

## 再測定

2 週間ごとに追記する。取得方法は [ホスト容量](../docs/host-capacity.md) の「再測定」と `kubectl describe node`。
ホストの値は Grafana / VMUI の `job="host-node"` でも確認できる。

| 日付 | ホスト available | ホスト swap | ホスト CPU PSI avg300 | CP CPU requests | CP メモリ実使用 | worker2 ディスク | etcd DB | 発火中アラート |
|---|---|---|---|---|---|---|---|---|
| 2026-11-14（作業前） | 2.8GiB | 1.9GiB | 8.7% | 93% | 68% | 72% | 231MB | Watchdog のみ |
| 2026-11-14（作業後、再起動から約1.5時間） | 2.4GiB | 0.66GiB | 7.3% | 75% | 69% | 43% | 32MB | Watchdog、CameraPartialSegment（再起動による） |

ホストの swap は再起動で 0 に戻った後、再び増えている。VM 合計 13GiB に対し実メモリ 15GiB で、前回と同じ水準（約 1.9GiB）まで戻る見込み。

## 更新履歴

| 日付 | 内容 |
|---|---|
| 2026-11-14 | 作成 |
| 2026-11-14 | OCI 関連（C-6、F-1、F-2）を当面扱わないと決定。C-4 / C-5 は VM 内 sudo で実行できるため担当を修正 |
| 2026-11-14 | Phase A〜D を実施。B-6 を追加。調査結果・障害記録・判断 6〜8 を追記 |
