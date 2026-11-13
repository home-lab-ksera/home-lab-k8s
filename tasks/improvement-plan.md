# 全体改善計画：重要 Pod の保護・依存更新・監視範囲・アプリ整理

作成日: 2026-11-14

状態: 計画。未着手。進行状況は [進行状況管理票](improvement-progress.md) で管理する。

[全体最適化計画](capacity-optimization-plan.md)（Phase 0〜3 実施済み）の後続。容量の削減は完了しており、
本計画は 2026-11-14 のレビューで見つかった残課題を扱う。[OCI単一クラスタ計画](oci-single-cluster-plan.md) は置き換えない。

## 1. 目的

| 項目 | 成果 |
|---|---|
| 重要 Pod の保護 | メモリ圧迫時に Gateway・DNS・GitOps・DB が先に evict されない |
| 依存更新 | 9 リポジトリで Renovate が実際に PR を作り、既知の遅れと脆弱性を解消する |
| Rust の検証 | テストとリリースを同じコンパイラにし、依存脆弱性を CI で検出する |
| 監視範囲 | ホスト・Job 失敗・外形の疎通を監視対象に入れ、手動確認をなくす |
| ホスト | NFS の公開範囲を VM に限定し、sudo が必要な残作業を完了する |
| アプリ整理 | 巨大ファイル・名前と役割のずれ・Pod のセキュリティ設定を順に解消する |

対象外（決定済みのため再提案しない）: ホストの有線化、オフサイトバックアップ、Kubernetes のホスト直接実行、
Flannel の置換、Harbor・監視基盤の置換。

## 2. 実測した現状（2026-11-14 00:35 JST）

| 項目 | 値 |
|---|---|
| ArgoCD | 49 Application すべて Synced / Healthy。発火中アラートは Watchdog のみ |
| ホスト | available 2.8GiB、swap 1.9GiB（増減なし）、memory PSI 0、CPU PSI some avg300 8.7% |
| ホスト CPU | guest 38% + sys 14%（4 コア中 約 2.1 コア）。Pod の実使用合計は約 1.1 コア |
| control-plane | CPU requests 1680m / 1800m（93%）、実使用 492m。メモリ 68%。CPU PSI 約 20% |
| worker2 | メモリ requests 62%、実使用 53%。ルートディスク 72%（containerd 34GB） |
| BestEffort の Pod | ArgoCD 7 Pod、`nginx-gateway-nginx`、Tailscale operator、local-path / NFS provisioner、pgBackRest repo-host |
| PriorityClass | system 既定の 2 つのみ |
| CoreDNS | 2 replica とも control-plane 上 |
| kube-apiserver | 1.19GiB（control-plane VM の 24%）。CRD 80、etcd DB 231MB |
| Renovate | App は導入済み（対象は selected）。8 アプリリポジトリで PR・Dashboard とも 0 件 |
| 依存の遅れ | blog / cookLog `node:20`・`nginx:1.27`、nature-remo `go 1.23`、hitomi `rust-toolchain 1.88.0` と Dockerfile `1.99.1` |
| Rust CI | 4 リポジトリとも `dtolnay/rust-toolchain@stable`。clippy / fmt / cargo-audit なし |
| NFS export | `nfs-share *(rw,no_root_squash)`、`/mnt/k8s-storage 192.168.10.0/24(rw,no_root_squash)`。ufw は有効だがルール未確認 |
| ホスト残作業 | `/var/run/reboot-required` あり（稼働 22 日）、kubectl 1.33、snapd / fwupd / docker 稼働中 |
| 監視 | アラート 40 件。ホスト・VM の node メトリクス、Job 失敗、外形の疎通はなし |
| Job | hitomi-monitor が OOM で 3 回連続失敗（#155 反映後の直近 1 回は成功）。手動 Job `oci-always-free` が 10 日 Failed |
| Pod セキュリティ | blog、cooklog、hitomi-monitor、hitomi-upload-viewer、監視 4 Pod に securityContext なし。`local-path-storage` / `nfs-storage` / `postgres-operator` に PSA ラベルなし |
| control-plane ディスク | `/var/log` 5.0GB |

## 3. 実施順序

ID は [進行状況管理票](improvement-progress.md) と対応する。担当の「管理者」は sudo や外部サービスの操作が必要なもの。

### Phase A: 重要 Pod の保護とホスト（1週目）

| ID | 内容 | 担当 | 完了条件 | 切戻し |
|---|---|---|---|---|
| A-1 | PriorityClass `homelab-platform`（system より低く既定より高い値）を `manifests/core/` に追加し、Gateway control plane、Split DNS、shared-postgresql、Harbor registry / core に付与。Gateway data plane は chart に指定欄がなく、Tailscale の ProxyClass は公開経路の契約チェックが未対応のため対象外とし、A-2 の requests で保護する | GitOps | 対象 Pod の `priorityClassName` が反映、全 Application Healthy | PR revert |
| A-2 | ArgoCD 7 Pod、`nginx-gateway-nginx`、Tailscale operator、両 provisioner に実測ベースの requests（メモリは実使用の約 1.2 倍、CPU は 10〜50m）を設定 | GitOps | 該当 Pod の QoS が Burstable、ノード requests が allocatable 内 | PR revert |
| A-3 | control-plane の requests を実測へ寄せる。MetalLB controller / speaker 各 100m→25m、kube-network-policies 100m→50m。CoreDNS は 1 replica を worker2 へ分散（topologySpread）するか 1 replica にする | GitOps。CoreDNS は kubeadm 管理のため手順を別記 | control-plane の CPU requests 80% 未満、DNS 解決が両ノードで成功 | PR revert、CoreDNS は `kubectl scale` で戻す |
| A-4 | NFS export を `192.168.122.0/24` のみに絞る。先に `sudo ufw status verbose` で 2049 / 111 の公開範囲を記録する | 管理者 | `showmount -e` が VM サブネットのみ、全 PVC が Bound のまま、LAN 端末から mount 不可 | `/etc/exports` を戻して `exportfs -ra` |
| A-5 | ホスト再起動（kernel 更新の反映）。前に repo1 の直近 backup 成功を確認し、後に `make recover` 相当で Node Ready を待つ | 管理者 | `reboot-required` が消える、全 Application Healthy | なし（再起動のみ） |
| A-6 | ホストの kubectl を 1.36 系へ更新、snapd / fwupd を停止。docker は利用有無を確認してから判断 | 管理者 | `kubectl version` の skew 警告なし | パッケージを戻す |

A-1 と A-2 は同じ PR にしない。A-2 の requests で配置できない Pod が出ないことを、A-3 の削減後の数値で先に計算する。
A-5 は SSH が Wi-Fi 経由のため、wpa_supplicant / NetworkManager には触れない。

### Phase B: 依存更新と CI（1〜2週目、Phase A と並行）

| ID | 内容 | 担当 | 完了条件 | 切戻し |
|---|---|---|---|---|
| B-1 | Renovate App の対象リポジトリに 8 アプリリポジトリが入っているか確認し、Dependency Dashboard を出す | 管理者（GitHub の App 設定） | 8 リポジトリに Dashboard issue が作られる | なし |
| B-2 | 初回の遅れを解消。blog / cookLog の Node と nginx、nature-remo の `go` 宣言、各 caller の `actions/checkout` | 各アプリの PR | Trivy の HIGH / CRITICAL が blog / cooklog で 0、publish 成功 | PR revert |
| B-3 | Rust 4 リポジトリに `rust-toolchain.toml` を置き Dockerfile と同じ版にする。CI は `@stable` をやめてこのファイルに従う | 各アプリの PR | テストとイメージのコンパイラ版が一致 | PR revert |
| B-4 | home-lab-k8s に共通 Rust 検証 workflow（fmt、clippy、test、cargo-audit）を `workflow_call` で追加し、4 リポジトリから呼ぶ。clippy と audit は報告のみで開始 | GitOps + 各アプリの PR | 4 リポジトリで実行され、結果が summary に出る | 呼び出しを外す |
| B-5 | B-4 の結果を見て、audit をゲート化するか決める | 判断 | 判断を管理票に記録 | - |

### Phase C: 監視範囲とディスク（2〜3週目）

| ID | 内容 | 担当 | 完了条件 | 切戻し |
|---|---|---|---|---|
| C-1 | Job 失敗アラート（`kube_job_status_failed`、CronJob 由来を対象）を追加 | GitOps | 試験用の失敗 Job で発火し Slack に届く | PR revert |
| C-2 | ホストに node_exporter を入れ、`192.168.122.1:9100` を VM サブネットからのみ scrape。swap・PSI・ディスク・steal のアラートを追加 | 管理者（導入）+ GitOps（scrape・ルール） | VictoriaMetrics でホストの swap / PSI が見える | scrape 設定を revert、サービス停止 |
| C-3 | 外形の疎通監視（Split DNS、Gateway の TLS 期限、主要アプリの HTTP 応答）。新しい常駐 Pod を増やさず、既存の CronJob か Alloy の機能で実装する | GitOps | Gateway を止める試験で 15 分以内に発火 | PR revert |
| C-4 | etcd の defrag と、定期実行の要否判断。control-plane `/var/log` 5GB の内訳確認と保持設定 | VM 内作業（VM は sudo 可） | etcd DB が 100MB 未満、ログ保持の方針を記録 | なし |
| C-5 | worker2 の image GC 閾値を下げる（kubelet drop-in、`imageGCHighThresholdPercent` 70 / Low 60） | VM 内作業 + Terraform 定義 | ルートディスク 65% 未満で安定 | drop-in を削除 |
| C-6 | 失敗した手動 Job `oci-always-free` と PVC の扱いを決める | 判断 | 削除または Git 管理化 | - |

### Phase D: セキュリティ設定と調査（1〜2か月）

| ID | 内容 | 担当 | 完了条件 |
|---|---|---|---|
| D-1 | blog、cooklog、hitomi-monitor、hitomi-upload-viewer に securityContext（非 root、権限昇格なし、capabilities drop、seccomp）。nginx は unprivileged イメージへ | GitOps + アプリの Dockerfile | `sandbox` を PSA restricted で enforce できる |
| D-2 | 監視 4 Pod（VictoriaMetrics、vmalert、Alertmanager、kube-state-metrics）に同じ設定 | GitOps | `observability` を PSA restricted で enforce できる |
| D-3 | `local-path-storage` / `nfs-storage` / `postgres-operator` に PSA ラベル | GitOps | ラベル付与後も Pod が起動 |
| D-4 | kube-apiserver のメモリ 1.19GiB の原因調査（watch cache、監査ログ、CRD 数、`GOMEMLIMIT`） | 調査 | 原因と対処の要否を記録 |
| D-5 | 10〜30 分毎の CronJob 3 本（harbor-maintenance、rustfs-scanner-monitor、postgresql-credentials）の Pod 生成コストを測り、間引くか常駐化するか決める | 調査 | 実測と判断を記録 |
| D-6 | ドキュメントの修正（`docs/architecture.md` の図、容量計画の状態欄） | GitOps | 現行構成と一致 |

### Phase E: アプリ整理（随時）

| ID | 内容 | 完了条件 |
|---|---|---|
| E-1 | 巨大ファイルの分割。hitomi-pdf-batch `media.rs`（1806 行）、home-camera `persistent.rs`（1271 行）、api-hub `s3_handler.rs` / `s3_service.rs`（各 1265 行） | 各ファイル 600 行以下、テスト成功 |
| E-2 | hitomi と hitomi-pdf-batch の `failures.rs` の重複を確認し、共通化するか決める | 判断を記録 |
| E-3 | hitomi-pdf-batch の名前と役割のずれ（media API / worker を含む）と `sandbox` namespace の改名を、まとめて行うか決める | 判断を記録 |

### Phase F: 長期

| ID | 内容 | 完了条件 |
|---|---|---|
| F-1 | OCI 計画を続けるか、ホストの RAM 増設へ切り替えるかを決める | 判断を記録 |
| F-2 | 続ける場合は [OCI単一クラスタ計画](oci-single-cluster-plan.md) の Phase 0（読み取り調査）のみ実施 | G0 の記録 |

## 4. 受け入れ基準

| 項目 | 基準 | 確認方法 |
|---|---|---|
| 重要 Pod | Gateway・ArgoCD・DNS・DB が Burstable 以上で PriorityClass 付き | `kubectl get pods -A -o custom-columns=...qosClass,priorityClassName` |
| control-plane | CPU requests 80% 未満 | `kubectl describe node k8s-control-plane` |
| 依存更新 | 9 リポジトリに Dashboard、既知の遅れ 4 件が解消 | 各リポジトリの issue / PR |
| Rust | 4 リポジトリで toolchain 一致、audit が実行される | workflow の summary |
| NFS | export が VM サブネットのみ | `showmount -e 192.168.10.11` |
| 監視 | ホスト swap / PSI、Job 失敗、疎通が発火試験に合格 | 試験の記録 |
| ディスク | worker2 ルート 65% 未満、etcd DB 100MB 未満 | `df`、etcd メトリクス |

## 5. 進め方

- Kubernetes リソースの変更は `manifests/` の PR で行い、`automation/scripts/ci/validate.sh` を通す。
- 1 PR 1 項目。完了したら管理票の状態・PR 番号・完了日を更新する。
- 判断が必要な項目は、決めた内容と日付を管理票の「判断記録」に残す。
- 2 週間ごとに管理票の「再測定」を更新する。
