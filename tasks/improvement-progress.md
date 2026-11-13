# 進行状況管理票：全体改善計画

対象: [全体改善計画](improvement-plan.md)

最終更新: 2026-11-14

状態の値: `未着手` / `進行中` / `確認待ち`（反映済みで完了条件の確認前） / `完了` / `保留`（判断または前提待ち） / `取り下げ`

## 集計

| Phase | 項目数 | 完了 | 進行中 | 保留 |
|---|---:|---:|---:|---:|
| A 重要 Pod の保護とホスト | 6 | 0 | 0 | 0 |
| B 依存更新と CI | 5 | 0 | 0 | 0 |
| C 監視範囲とディスク | 6 | 0 | 0 | 1 |
| D セキュリティ設定と調査 | 6 | 0 | 0 | 0 |
| E アプリ整理 | 3 | 0 | 0 | 0 |
| F 長期 | 2 | 0 | 0 | 2 |
| 合計 | 28 | 0 | 0 | 3 |

## 項目

### Phase A

| ID | 内容 | 担当 | 状態 | 開始条件 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|---|
| A-1 | PriorityClass の追加と付与 | GitOps | 未着手 | なし | | | |
| A-2 | BestEffort の Pod に requests | GitOps | 未着手 | A-3 の数値を計算済み | | | ArgoCD 7、Gateway、Tailscale operator、provisioner 2 |
| A-3 | control-plane の requests 削減、CoreDNS の配置 | GitOps | 未着手 | なし | | | 現在 93% |
| A-4 | NFS export を VM サブネットに限定 | 管理者 | 未着手 | ufw のルールを記録 | | | sudo が必要 |
| A-5 | ホスト再起動 | 管理者 | 未着手 | repo1 の backup 成功を確認 | | | sudo が必要。SSH は Wi-Fi 経由 |
| A-6 | kubectl 1.36、不要サービス停止 | 管理者 | 未着手 | なし | | | 前計画 Phase 0 からの持ち越し |

### Phase B

| ID | 内容 | 担当 | 状態 | 開始条件 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|---|
| B-1 | Renovate App の対象確認と Dashboard | 管理者 | 未着手 | なし | | | 8 リポジトリで 0 件 |
| B-2 | 既知の遅れを解消 | 各アプリ | 未着手 | B-1 | | | 下の「リポジトリ別」で管理 |
| B-3 | `rust-toolchain.toml` の統一 | 各アプリ | 未着手 | なし | | | 下の「リポジトリ別」で管理 |
| B-4 | 共通 Rust 検証 workflow | GitOps + 各アプリ | 未着手 | B-3 | | | 下の「リポジトリ別」で管理 |
| B-5 | audit のゲート化判断 | 判断 | 未着手 | B-4 の結果 | | | |

リポジトリ別（B-2〜B-4）。対象外は `-`。

| リポジトリ | B-1 Dashboard | B-2 遅れ解消 | B-3 toolchain | B-4 共通検証 |
|---|---|---|---|---|
| api-hub | 未 | checkout | 未 | 未 |
| hitomi | 未 | checkout | 未（1.88.0 → Dockerfile と一致） | 未 |
| home-camera | 未 | checkout | 未 | 未 |
| hitomi-pdf-batch | 未 | checkout | 未（CI 側のみ） | 未 |
| nature-remo | 未 | `go` 宣言、checkout | - | - |
| blog | 未 | Node、nginx、checkout | - | - |
| cookLog | 未 | Node、nginx、checkout | - | - |
| RSSHub-private | 未 | -（upstream 取り込みで更新） | - | - |

### Phase C

| ID | 内容 | 担当 | 状態 | 開始条件 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|---|
| C-1 | Job 失敗アラート | GitOps | 未着手 | なし | | | |
| C-2 | ホストの node_exporter とアラート | 管理者 + GitOps | 未着手 | なし | | | 導入は sudo が必要 |
| C-3 | 外形の疎通監視 | GitOps | 未着手 | なし | | | 常駐 Pod を増やさない |
| C-4 | etcd defrag、control-plane のログ保持 | VM 内作業 | 未着手 | なし | | | etcd DB 231MB、`/var/log` 5.0GB。VM 内は sudo 可、ホストの sudo は不要 |
| C-5 | worker2 の image GC 閾値 | VM 内作業 + Terraform | 未着手 | なし | | | containerd 34GB。VM 内は sudo 可、ホストの sudo は不要 |
| C-6 | `oci-always-free` Job と PVC の扱い | 判断 | 保留 | OCI 関連の再開 | | | 2026-11-14: OCI 関連は当面扱わない |

### Phase D

| ID | 内容 | 担当 | 状態 | 開始条件 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|---|
| D-1 | sandbox 4 Pod の securityContext | GitOps + アプリ | 未着手 | B-2（nginx の更新と同時が効率的） | | | |
| D-2 | 監視 4 Pod の securityContext | GitOps | 未着手 | なし | | | |
| D-3 | 3 namespace に PSA ラベル | GitOps | 未着手 | なし | | | |
| D-4 | kube-apiserver メモリの調査 | 調査 | 未着手 | なし | | | 1.19GiB |
| D-5 | 高頻度 CronJob のコスト測定 | 調査 | 未着手 | C-2（ホスト CPU が見えてから） | | | |
| D-6 | ドキュメントの修正 | GitOps | 未着手 | なし | | | |

### Phase E

| ID | 内容 | 担当 | 状態 | 開始条件 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|---|
| E-1 | 巨大ファイルの分割 | 各アプリ | 未着手 | B-4（clippy が動いてから） | | | 3 リポジトリ 4 ファイル |
| E-2 | `failures.rs` の重複確認 | 調査 | 未着手 | なし | | | |
| E-3 | 名前と役割のずれ、namespace 改名 | 判断 | 未着手 | なし | | | |

### Phase F

| ID | 内容 | 担当 | 状態 | 開始条件 | PR / 記録 | 完了日 | メモ |
|---|---|---|---|---|---|---|---|
| F-1 | OCI 継続か RAM 増設か | 判断 | 保留 | OCI 関連の再開 | | | 2026-11-14: OCI 関連は当面扱わない |
| F-2 | OCI 計画 Phase 0 | 調査 | 保留 | F-1 | | | |

## 判断記録

| No | 判断事項 | 選択肢 | 決定 | 決定日 |
|---|---|---|---|---|
| 1 | `oci-always-free` の失敗 Job と PVC | 削除 / Git 管理化して再実行 / 現状維持 | 現状維持（OCI 関連は当面扱わない） | 2026-11-14 |
| 2 | 長期の方向 | OCI 計画を続ける / RAM 増設を先に検討 / 両方見送り | OCI 関連は当面扱わない。RAM 増設は未決 | 2026-11-14 |
| 3 | ホストの sudo が必要な作業（A-4〜A-6、C-2 の導入）の進め方 | 手順書を用意して管理者が実行 / 他の方法 | 未決 | |
| 4 | cargo-audit のゲート化（B-5） | 報告のみ / 失敗で publish を止める | 未決 | |
| 5 | CoreDNS の配置（A-3） | 2 replica を分散 / 1 replica | 未決 | |

## 再測定

2 週間ごとに追記する。取得方法は [ホスト容量](../docs/host-capacity.md) の「再測定」と `kubectl describe node`。

| 日付 | ホスト available | ホスト swap | ホスト CPU PSI avg300 | CP CPU requests | CP メモリ実使用 | worker2 ディスク | etcd DB | 発火中アラート |
|---|---|---|---|---|---|---|---|---|
| 2026-11-14 | 2.8GiB | 1.9GiB | 8.7% | 93% | 68% | 72% | 231MB | Watchdog のみ |

## 更新履歴

| 日付 | 内容 |
|---|---|
| 2026-11-14 | 作成 |
| 2026-11-14 | OCI 関連（C-6、F-1、F-2）を当面扱わないと決定。C-4 / C-5 は VM 内 sudo で実行できるため担当を修正 |
