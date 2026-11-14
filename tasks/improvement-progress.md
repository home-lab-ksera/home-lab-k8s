# 進行状況管理票：全体改善計画

対象: [全体改善計画](improvement-plan.md)

最終更新: 2026-11-14

状態の値: `未着手` / `進行中` / `確認待ち`（反映済みで完了条件の確認前） / `完了` / `保留`（判断または前提待ち） / `取り下げ`

## 集計

| Phase | 項目数 | 完了 | 進行中・確認待ち | 未着手 | 保留 | 取り下げ |
|---|---:|---:|---:|---:|---:|---:|
| A 重要 Pod の保護とホスト | 6 | 6 | 0 | 0 | 0 | 0 |
| B 依存更新と CI | 6 | 5 | 0 | 0 | 1 | 0 |
| C 監視範囲とディスク | 6 | 5 | 0 | 0 | 1 | 0 |
| D セキュリティ設定と調査 | 10 | 10 | 0 | 0 | 0 | 0 |
| E アプリ整理 | 3 | 2 | 0 | 0 | 0 | 1 |
| F 長期 | 2 | 0 | 0 | 0 | 2 | 0 |
| 合計 | 33 | 28 | 0 | 0 | 4 | 1 |

保留の 4 件は、Renovate の経過観察（B-1）と OCI 関連（C-6、F-1、F-2）。

## 項目

### Phase A

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| A-1 | PriorityClass の追加と付与 | GitOps | 完了 | #158 | 2026-11-14 | Gateway control plane、Split DNS、PostgreSQL、Harbor core / registry に付与。Gateway data plane と Tailscale は対象外（計画 A-1）。反映順の不備で DB が約4分停止（下の「障害記録」） |
| A-2 | BestEffort の Pod に requests | GitOps | 完了 | #161、#175 | 2026-11-14 | ArgoCD 7、Gateway data plane、Tailscale operator、provisioner 2、pgBackRest repo host。Gateway は chart のキー誤り（`nginx.resources`）を修正。残る BestEffort は kubeadm 管理の kube-proxy のみ（system-node-critical） |
| A-3 | control-plane の requests 削減、CoreDNS の配置 | GitOps | 完了 | #161 | 2026-11-14 | CPU requests 93% → 75%。CoreDNS は `rollout restart` で 2 ノードへ分散（Git 変更なし。kubeadm 管理） |
| A-4 | NFS export を VM サブネットに限定 | 管理者 | 完了 | ホスト作業 | 2026-11-14 | export は `nfs-share 192.168.122.0/24` の 1 行。作業中に export が 0 件になり NFS 依存 Pod が停止（「障害記録」） |
| A-5 | ホスト再起動 | 管理者 | 完了 | ホスト作業 | 2026-11-14 | kernel 7.0.0-43。両 VM は autostart で復帰 |
| A-6 | kubectl 1.36、不要サービス停止 | 管理者 | 完了 | ホスト作業 | 2026-11-14 | kubectl 1.36.0、fwupd と docker を停止・無効化。snapd はデスクトップ用 snap を管理しているため稼働のまま |

### Phase B

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| B-1 | Renovate App の対象確認と Dashboard | 管理者 | 保留 | 判断 9 | | 導入から 2 日のため経過を見る。次回の再測定時に Dashboard の有無を確認する |
| B-2 | 既知の遅れを解消 | 各アプリ | 完了 | 下表 | 2026-11-14 | Node / nginx / go / `actions/checkout` を解消 |
| B-3 | `rust-toolchain.toml` の統一 | 各アプリ | 完了 | 下表 | 2026-11-14 | 4 リポジトリとも 1.99.1。テストは `@stable` をやめこの版で実行 |
| B-4 | 共通 Rust 検証 workflow | GitOps + 各アプリ | 完了 | #159、#164、下表 | 2026-11-14 | fmt・clippy・cargo-audit（報告のみ）。指摘は job summary と job ログに出る |
| B-5 | audit のゲート化判断 | 判断 | 完了 | #174、api-hub#23、hitomi#37、home-camera#18、hitomi-pdf-batch#17 | 2026-11-14 | fmt・clippy・cargo-audit のいずれかに指摘があると `verify` が失敗し、publish されない。ビルドに含まれない sqlx の未使用 backend 2 件は各リポジトリの `.cargo/audit.toml` で理由付きで無視 |
| B-6 | cargo-audit / clippy の指摘解消（B-4 の結果で追加） | 各アプリ | 完了 | 下表 | 2026-11-14 | 4 リポジトリとも fmt・clippy は指摘なし。audit は対処不要分のみ |

リポジトリ別。対象外は `-`。

| リポジトリ | B-1 Dashboard | B-2 遅れ解消 | B-3 toolchain | B-4 共通検証 | B-6 指摘解消 |
|---|---|---|---|---|---|
| api-hub | 未 | 済（#21） | 済（#19） | 済（#19） | 済（#20、rustls 系の更新、clippy 7 件、fmt） |
| hitomi | 未 | 済（#36） | 済（#35、1.88.0 → 1.99.1） | 済（#35） | 済（#36、rustls 0.21 → 0.23、clippy 6 件）。実際の HTTPS 先で、正しい証明書の受理と自己署名・ホスト名違い・期限切れの拒否を確認 |
| home-camera | 未 | 済（#17） | 済（#15） | 済（#15） | 済（#16、reqwest 0.12） |
| hitomi-pdf-batch | 未 | 済（#15） | 済（#14） | 済（#14） | 済（#16、reqwest 0.12、quick-xml 0.41） |
| nature-remo | 未 | 済（#4、`go 1.26`） | - | - | - |
| blog | 未 | 済（#5、Node 26・nginx 1.30） | - | - | - |
| cookLog | 未 | 済（#6、Node 26・nginx 1.30）。`Dockerfile.slack` の Node 22 は Renovate に任せる | - | - | - |
| RSSHub-private | 未 | - | - | - | - |

cargo-audit に残る指摘（対処不要と判断したもの）: `libsqlite3-sys` と `rsa` は sqlx の未使用 backend が lockfile に載るだけでビルドに含まれない。
`rand` の unsound 警告と `paste` の unmaintained 警告（hitomi）は推移的依存。
hitomi は `cargo build --no-default-features` が通らない（`image` の feature 分岐が不完全）。既定の feature では問題なく、今回は扱っていない。

### Phase C

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| C-1 | Job 失敗アラート | GitOps | 完了 | #162 | 2026-11-14 | `KubernetesCronJobFailed`。NFS 停止中に失敗した Job 2 件で実際に発火し、解消した |
| C-2 | ホストの node_exporter とアラート | 管理者 + GitOps | 完了 | #162 | 2026-11-14 | `Host*` 5 件。VictoriaMetrics でホストの MemAvailable を取得できることを確認 |
| C-3 | 外形の疎通監視 | GitOps | 完了 | #166 | 2026-11-14 | Alloy 内蔵の blackbox exporter。8 URL と Split DNS が `probe_success=1`。発火試験: テスト用 target の失敗データを 8 分流し、5 分後に `EndpointProbeFailed` が発火して Alertmanager の slack 受信先へ渡ることを確認（Alertmanager のエラーなし） |
| C-4 | etcd defrag、control-plane のログ保持 | VM 内作業 | 完了 | #160 | 2026-11-14 | etcd DB 206MB → 18MB。journal は 500MB 上限（control-plane 3.9GB → 0.45GB）。監査ログ 1.1GB は設定どおり（D-4） |
| C-5 | worker2 の image GC 閾値 | VM 内作業 + Terraform | 完了 | #160 | 2026-11-14 | 70% / 60%。ルートディスク 72% → 43% |
| C-6 | `oci-always-free` Job と PVC の扱い | 判断 | 保留 | 判断 1 | | OCI 関連は当面扱わない |

### Phase D

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| D-1 | sandbox 4 Pod の securityContext | GitOps + アプリ | 完了 | #163、#167、#168、blog#5、cookLog#6、hitomi-pdf-batch#15 | 2026-11-14 | blog / cooklog は uid 101・読み取り専用 rootfs、viewer は uid 65532 で稼働を確認。hitomi-monitor は新しい権限設定の定期実行（18:55 JST）が成功。Selenium sidecar は対象外のため `sandbox` は baseline のまま |
| D-2 | 監視 4 Pod の securityContext | GitOps | 完了 | #165 | 2026-11-14 | VictoriaMetrics は NFS 上のデータが root 所有のため root のまま権限だけ落とした。`observability` は baseline のまま |
| D-3 | 3 namespace に PSA ラベル | GitOps | 完了 | #160 | 2026-11-14 | local-path-storage privileged、nfs-storage baseline、postgres-operator restricted |
| D-4 | kube-apiserver の負荷調査と監査ログの絞り込み | 調査 + VM 内作業 | 完了 | #173、下の「調査結果」 | 2026-11-14 | 監査ログ 約 107MB / 時 → 約 28MB / 時。保持は約 10 時間 → 約 35 時間 |
| D-5 | 高頻度 CronJob のコスト測定 | 調査 | 完了 | 下の「調査結果」 | 2026-11-14 | 対処不要 |
| D-6 | ドキュメントの修正 | GitOps | 完了 | #160 | 2026-11-14 | |
| D-8 | hitomi の未使用 feature を削除（作業中に発見して追加） | アプリ | 完了 | hitomi#37 | 2026-11-14 | `image-processing` はコードに分岐がなく、`--no-default-features` でビルドできなかった。`image` を必須の依存にした |
| D-9 | api-hub の不安定なメモリ検査（作業中に発見して追加） | アプリ | 完了 | api-hub#22 | 2026-11-14 | 64MiB と 256MiB の peak RSS の差が実行ごとに 8〜32MiB 揺れ、上限 32MiB ちょうどで失敗していた。上限を part buffer 8 個分（64MiB）にした。転送量に比例する増加（約 192MiB）は検出できる |
| D-10 | カメラの破損 segment の隔離（作業中に発見して追加） | 運用 | 完了 | `/spool/.quarantine/20261114-4cd14ea4-…/incident.json` | 2026-11-14 | 00:51 JST のホスト再起動時に録画中だった 1 本（78MB、moov atom なし）。`docs/state.md` の手順で原本・CSV・checksum を保全して隔離し、`CameraPartialSegment` が解消 |
| D-7 | Image Updater の再試行ループ解消（作業中に発見して追加） | GitOps | 完了 | #170 | 2026-11-14 | 古い digest の失敗 Job が残り、hitomi の更新を約2分おきに 11 時間で 307 回試みていた。失敗 Job 3 件を削除し、hitomi 系 CronJob 4 本に `ttlSecondsAfterFinished: 86400` を設定 |

### Phase E

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| E-1 | 巨大ファイルの分割 | 各アプリ | 完了 | home-camera#19、api-hub#24、hitomi-pdf-batch#18 | 2026-11-14 | 4 ファイルを役割ごとの module へ分割。最大は api-hub `s3_service/mod.rs` 522 行、テストは hitomi-pdf-batch `tests/recovery.rs` 510 行。処理と公開 API は変えていない |
| E-2 | `failures.rs` の重複確認 | 調査 | 完了 | 判断 8 | 2026-11-14 | 公開関数 `list` / `history` / `act` が同名・同シグネチャで、同一行は 152 行。共通化しない（判断 8） |
| E-3 | 名前と役割のずれ、namespace 改名 | 判断 | 取り下げ | 判断 10 | 2026-11-14 | 実施しない（判断 10） |

### Phase F

| ID | 内容 | 担当 | 状態 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|
| F-1 | OCI 継続か RAM 増設か | 判断 | 保留 | 判断 2 | | OCI は当面扱わない。RAM 増設は今はできない |
| F-2 | OCI 計画 Phase 0 | 調査 | 保留 | | | |

## 残作業（次に行うこと）

| 順 | 内容 | 担当 |
|---|---|---|
| 1 | Renovate の Dashboard が作られたかを次回の再測定時に確認（B-1） | - |
| 2 | 次回の再測定（2026-11-28 ごろ）で、監査ログの保持時間と kube-apiserver の CPU / メモリを記録する | - |

上記以外に未着手・進行中の項目はない。

## 調査結果

### D-4 kube-apiserver（1.2GiB、150m）

- 監査ポリシーは全リクエストを `Metadata` で記録している。監査ログは約 107MB / 時で、100MB × 10 世代の上限により保持は約 10 時間。
- 直近 22 分（29,200 件）の内訳: Alloy の `get pods` と Pod ログ取得が 24%、ESO の Secret の get / list が 12%、各 controller の lease 更新が約 15%、匿名の health 確認が 3%。
- Alloy は `loki.source.kubernetes` で、全 Pod のログを API サーバー経由で取得している（毎秒約 4 リクエスト）。ログの流量はすべて API サーバーを通る。
- watch は 742 本。CRD は 80。メモリの内訳までは切り分けていない。

対処（判断 6・7）:

1. 監査ポリシーを絞った（#173）。health 確認と metrics、lease、ServiceAccount・kubelet・control-plane コンポーネントによる
   Secret 以外の読み取りを記録しない。すべての変更、人の操作、Secret へのアクセスは記録する。
   2026-11-14 21:13 JST に Control Plane へ反映し API サーバーを再起動した（約 10 秒で復帰）。
   反映後 100 秒の実測は 0.79MB（約 28MB / 時）で、残りは ESO の Secret 取得と ExternalSecret の状態更新が中心。
2. Alloy の収集方式は変えない。監査ログへの影響は 1 で解消した。DaemonSet にすると Pod が 1 つ増え、
   hostPath のために `observability` の PSA を緩める必要がある。kube-apiserver は反映後 140m / 1.07GiB で、
   次回の再測定で CPU が下がっていなければ再検討する。

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
| 4 | cargo-audit のゲート化（B-5） | 報告のみ / 失敗で publish を止める | fmt・clippy・audit とも失敗で publish を止める | 2026-11-14 |
| 5 | CoreDNS の配置（A-3） | 2 replica を分散 / 1 replica | 2 replica を分散 | 2026-11-14 |
| 6 | 監査ポリシーの絞り込み（D-4） | 現状維持 / 読み取りと lease を除外 | 機械的な読み取り・lease・health を除外。Secret へのアクセスは残す | 2026-11-14 |
| 7 | Alloy のログ収集方式（D-4） | 現状維持（API 経由） / DaemonSet でファイルを読む | 現状維持。次回の再測定で再検討 | 2026-11-14 |
| 8 | `failures.rs` の共通化（E-2） | 現状維持 / 共通 crate に切り出す | 現状維持。利用側が 2 つで、別リポジトリ間の共有には crate の公開・版管理が新たに要る。3 つ目の利用側が出たら再検討 | 2026-11-14 |
| 9 | Renovate が PR を作らない件（B-1） | App の対象を確認 / 経過を見る | 導入 2 日のため経過を見る | 2026-11-14 |
| 10 | hitomi-pdf-batch の改名と `sandbox` namespace の改名（E-3） | 実施 / 見送り | 見送り。namespace を変えると全アプリの再作成、PVC（録画 100GiB など）の移し替え、Secret・NetworkPolicy・Harbor project・監視ルールの書き換えが要り、停止を伴う。得られるのは名前の整合だけ | 2026-11-14 |

## 再測定

2 週間ごとに追記する。取得方法は [ホスト容量](../docs/host-capacity.md) の「再測定」と `kubectl describe node`。
ホストの値は Grafana / VMUI の `job="host-node"` でも確認できる。

| 日付 | ホスト available | ホスト swap | ホスト CPU PSI avg300 | CP CPU requests | CP メモリ実使用 | worker2 ディスク | etcd DB | 発火中アラート |
|---|---|---|---|---|---|---|---|---|
| 2026-11-14（作業前） | 2.8GiB | 1.9GiB | 8.7% | 93% | 68% | 72% | 231MB | Watchdog のみ |
| 2026-11-14（作業後、再起動から約1.5時間） | 2.4GiB | 0.66GiB | 7.3% | 75% | 69% | 43% | 32MB | Watchdog、CameraPartialSegment（再起動による） |
| 2026-11-14（全項目の反映後、21:22 JST） | 2.2GiB | 0.71GiB | 9.8% | 76% | 73% | 42% | 32MB | Watchdog のみ |

ホストの swap は再起動で 0 に戻った後、再び増えている。VM 合計 13GiB に対し実メモリ 15GiB で、前回と同じ水準（約 1.9GiB）まで戻る見込み。

## 更新履歴

| 日付 | 内容 |
|---|---|
| 2026-11-14 | 作成 |
| 2026-11-14 | OCI 関連（C-6、F-1、F-2）を当面扱わないと決定。C-4 / C-5 は VM 内 sudo で実行できるため担当を修正 |
| 2026-11-14 | Phase A〜D を実施。B-6、D-7 を追加。調査結果・障害記録・判断 6〜8 を追記 |
| 2026-11-14 | A-6 完了（docker 停止）。B-1 は経過観察（判断 9）。hitomi-monitor の実行成功を確認 |
| 2026-11-14 | B-2、B-6 完了（hitomi の rustls 0.23 移行、checkout v6） |
| 2026-11-14 | B-5、E-1、判断 4・6・7・8・10 を完了。D-8〜D-10 を追加。監査ポリシーを反映 |
