# 全体最適化計画：ホスト容量・基盤の軽量化・2VM化・CI統合

作成日: 2026-11-10

改訂日: 2026-11-11（バックアップ強化（旧 1-c）を取り下げ。経緯は [6.4](#64-バックアップ強化取り下げ)）

状態: Phase 0 を2026-11-11に実施（[実施記録](#11-phase-0-実施記録2026-11-11)）。Phase 1 以降は未着手。
以下の判断は2026-11-10に確定した。

- Phase 2 は B案（control-plane をスケジュール可能にし worker を1台へ減らす2VM構成）を採用する。
- Harbor の Trivy を無効化し、脆弱性スキャンは GitHub Actions 側へ移す。
- バックアップ強化（OCI Object Storage へのオフサイト、etcd 日次 snapshot）は行わない。既存の pgBackRest repo1 を維持する。

[信頼性改善計画](cluster-reliability-improvement-plan.md)、[台帳CI計画](contract-ci-security-plan.md)、
[OCI単一クラスタ計画](oci-single-cluster-plan.md) は置き換えない。本計画はそれらの前段として
ホストの過積載を解消し、2VM 構成を OCI 計画の「自宅 worker 1台」へ接続する。

## 1. 目的と合意事項

ホスト1台（Intel N100 4C / 15.5GiB）に 3VM・合計 10vCPU / 16GiB を宣言し、ホストが常時 swap を使っている。
一方でコンテナの実メモリは約7.2GiBで、ユーザーアプリは約0.3GiBにすぎない。
本計画は「アプリを削る」のではなく「基盤と仮想化のオーバーヘッドを削る」ことで、
同じハードウェアで安定稼働と将来の OCI 拡張の余地を得る。

| 項目 | 成果 |
|---|---|
| ホスト容量 | swap 常用を解消し、ホストに 2GiB 以上の空きを恒常的に残す |
| 基盤の軽量化 | 不要コンポーネントの停止と定期処理の間引きで、基盤の常駐メモリと CPU を下げる |
| 2VM化 | worker1 を撤去し、control-plane をスケジュール可能にする。ゲスト1台分の RSS・vCPU・DaemonSet を削減する |
| データ配置 | PostgreSQL データを NFS（HDD, sync）から VM ディスク（NVMe）へ移す |
| CI統合 | 8リポジトリの publish workflow を共通化し、依存更新と Image Updater 由来の CI 実行を減らす |

対象外: Kubernetes をホスト直接実行へ変える（C案）、Flannel の置換、ArgoCD の Application 分割方針の変更、
`sandbox` namespace の改名（[台帳CI計画](contract-ci-security-plan.md) の構造改革で扱う）、
Harbor 自体の撤去、監視基盤の置換。

## 2. 実測した現状（2026-11-10 22:00 JST）

ホストは `ksera@192.168.10.11`、VM は `k8suser@192.168.122.10/.11/.12`。数値は `free`、`vmstat`、`/proc/pressure`、
`virsh dommemstat`、`kubectl describe node`、VictoriaMetrics の `container_memory_working_set_bytes` から取得した。

| 項目 | 値 |
|---|---|
| ホスト | Ubuntu 26.04.1、kernel 7.0、RAM 15.5GiB、swap 4GiB 中 2.7GiB 使用、load 5.2、CPU内訳 guest 46% / sys 19% / idle 34% |
| VM 宣言 | CP 2vCPU / 4096MiB、worker1・worker2 各 4vCPU / 6144MiB（[variables.tf](../automation/infrastructure/variables.tf)） |
| qemu RSS 合計 | 12.8GiB。VM 内の実使用は CP 2.2 / worker1 2.3 / worker2 2.7GiB（計7.2GiB）で、差分はゲストのページキャッシュ |
| コンテナ実メモリ | 約7.2GiB。kube-apiserver 1.4、RustFS 1.1、ArgoCD 1.0、監視 1.0、cert-manager 0.24、Tailscale 0.22、Harbor 0.19、sandbox アプリ 0.29 |
| CPU 実使用 | 最大は kube-apiserver 0.19 コア。クラスタ全体で 1 コア未満 |
| ノード requests | worker2 81%（limits 257%）、worker1 28%、CP 15%。sandbox の Pod は全て worker2 に配置 |
| Probe 失敗 | media-api、harbor-registry、rsshub-private で readiness / startup の timeout が発生（いずれも worker2） |
| Metrics API | 未導入。`kubectl top` 不可（[observability.md](../docs/observability.md) に記載） |
| ArgoCD | 48 Application 全て Synced / Healthy。ApplicationSet 0件。dex は GitHub SSO で使用中 |
| Harbor | 9 Pod。Trivy DB が NFS 上に 1.6GiB。`harbor-maintenance` CronJob は5分毎で、失敗 Pod が2件残存 |
| PostgreSQL | pgdata 22MB が `nfs-postgresql`（HDD RAID1、NFS export `sync`）上。repo1 も同じ NFS。再起動 12 回 |
| etcd snapshot | [upgrade-precheck.sh](../automation/scripts/upgrade/upgrade-precheck.sh) 実行時のみ。定期取得なし |
| NFS | ホスト RPC 1.67 億回、getattr 23%。RustFS 315GB、Harbor registry 2.4GiB、Trivy 1.6GiB |
| Git / CI | 直近30日 home-lab-k8s 272 commit、うち 88 件が Image Updater の `build: automatic update`。Deploy Process Validation 183 run |
| publish workflow | 8 リポジトリに 145〜280 行の類似 workflow。内容は全て異なる |
| 依存更新 | Renovate は home-lab-k8s のみ、dependabot は RSSHub-private のみ |
| 未マージ | RSSHub-private `ci/tailscale-oidc`（未 commit 変更あり）、hitomi-pdf-batch `fix/expected-upstream-responses`、cookLog の未 commit テスト |

上記のうち control-plane 静的 Pod の再起動回数（kube-controller-manager 28 回など）は VM 再起動（最終 2026-11-10 01:25 UTC）の累積であり、
クラッシュの根拠とはしない。

## 3. 目標構成

| 項目 | 現行 | 目標 |
|---|---|---|
| VM | CP 2vCPU/4GiB、worker×2 4vCPU/6GiB | CP 2vCPU/5GiB（スケジュール可能）、worker2 4vCPU/8GiB。合計 6vCPU / 13GiB |
| ホスト残メモリ | 約0.2GiB（swap 2.7GiB） | 2GiB 以上、swap 使用 256MiB 未満 |
| PostgreSQL データ | nfs-postgresql（NFS/HDD） | local-path（VM ディスク/NVMe）。バックアップは既存の repo1（NFS）のまま |
| Harbor | Trivy 有効、maintenance 5分毎 | Trivy 無効、maintenance 1時間毎。スキャンは Actions の trivy-action |
| ArgoCD | 7 Deployment | applicationset-controller 停止。reconciliation 300s |
| Metrics API | なし | metrics-server（kubelet serving 証明書を利用） |
| publish workflow | 8 本の独立 workflow | home-lab-k8s の `workflow_call` 共通 workflow を各リポジトリから呼ぶ |
| 依存更新 | 1 リポジトリ | 共通 Renovate preset を 9 リポジトリで参照 |

VM サイズの根拠: 2VM 化後の常駐は CP 側に control-plane 2.3GiB＋ArgoCD 1.0GiB、worker2 側に RustFS 1.1GiB＋監視 1.0GiB＋その他 1.5GiB を置く想定。
各 VM に OS 0.5GiB とページキャッシュ 1GiB 以上を残す。vCPU は 6 で物理 4 コアの 1.5 倍に収める。

## 4. 実施順序と PR 分割

| 順序 | 単位 | 開始条件 | 完了条件 | 切戻し |
|---|---|---|---|---|
| 0-a | ホスト即時対応（balloon で VM メモリ縮小、ホスト不要サービス停止） | なし | swap 使用が減少し、VM 内 MemAvailable が 1GiB 以上 | `virsh setmem` で元の値へ戻す |
| 0-b | GitOps 小修正（maintenance 間隔、failed Job 整理、topologySpread、metrics-server、kubectl 更新） | 0-a | Metrics API 応答、Probe 失敗イベントが 24h で 0 | 各 PR を revert |
| 1-a | 基盤の軽量化（Trivy 無効、applicationset 停止、reconciliation 延長） | 0-b | Harbor push / pull と ArgoCD 同期が従来どおり | PR revert。Trivy PVC は archive から復元 |
| 1-b | Image Updater 由来 CI の抑制 | なし | 自動 commit で重い job が走らない | PR revert |
| ~~1-c~~ | 取り下げ（[6.4](#64-バックアップ強化取り下げ)） | - | - | - |
| 1-d | PostgreSQL を local-path へ移行 | 移行直前の repo1 full backup 成功 | 新 instance set で稼働、旧 PVC は Retain で保持 | 旧 instance set へ switchover |
| 2-a | Terraform の worker を `for_each` 化（`moved` ブロック、plan に差分なし） | 1-a | `terraform plan` が No changes | 変更を revert |
| 2-b | 2VM 化（CP 再サイズと untaint、worker1 drain・削除） | 2-a、repo1 の直近バックアップ成功 | 全 Application Healthy、ホスト MemAvailable 2GiB 以上 | worker1 を Terraform で再作成し再 join |
| 3-a | 共通 publish workflow と trivy-action | 1-a | 8 リポジトリが共通 workflow を呼び、Harbor へ publish できる | 各リポジトリの旧 workflow へ戻す |
| 3-b | Renovate preset の共通化 | 3-a | 9 リポジトリで PR が作成される | 各リポジトリの設定を削除 |
| 3-c | branch 整理・保護、RSSHub-private の upstream 同期 | なし | 未マージ branch の判断記録、main の保護設定 | なし |
| 3-d | ドキュメント整理 | 2-b | docs/ が現行構成のみを記述し、旧手順の README を撤去 | なし |

Phase 0 は無停止、Phase 1 は Pod 単位の再起動、Phase 2 は control-plane の停止（VM 再起動、数分）を伴う。
Phase 3 は Phase 1 と並行して進める。

## 5. Phase 0: 即時対応

### 5.1 balloon による VM メモリ縮小（再起動なし）

memballoon は有効（`freePageReporting='on'`）なので、`currentMemory` を `virsh setmem <vm> <KiB> --live` で下げられる。
VM 内の available を 1GiB 以上残す値にする。

| VM | 現在 | 設定値 | 根拠 |
|---|---|---|---|
| k8s-control-plane | 4096MiB | 据え置き | available 1.7GiB で余裕が少ない |
| k8s-worker1 | 6144MiB | 4608MiB | available 3.6GiB。Phase 2 で撤去予定 |
| k8s-worker2 | 6144MiB | 5120MiB | available 3.2GiB |

これは runtime の値で Terraform の宣言とは一致しない。[host-capacity.md](../docs/host-capacity.md) に暫定値と理由を記録し、Phase 2 の再サイズで解消する。
`sudo` はホストで非対話実行できないため、`virsh` はユーザー権限で実行する。

### 5.2 ホストの不要サービス停止

cups、cups-browsed、bluetooth、ModemManager、avahi-daemon、fwupd、wpa_supplicant は VM 運用に不要。
`systemctl disable --now` で停止する。効果は 100MiB 程度で小さいが、再起動後の立ち上がりも軽くなる。

### 5.3 GitOps 小修正

- [maintenance.yaml](../manifests/platform/harbor/maintenance.yaml) の schedule を `*/5 * * * *` から `*/10 * * * *` へ。当初は1時間毎としたが、このJobは `latest` → `deploy` の昇格も担うため配布遅延の上限になる。`HarborRepositoryBlocked` の15分窓より短く保つ。
- 失敗した `oci-always-free` Job（Safety stop、7日経過）は status.json を確認し、必要資源が揃っていれば定義を外す。揃っていなければ原因を記録して再実行する。
- ~~`manifests/apps/*/` への `topologySpreadConstraints` 追加~~ は見送り。実メモリは worker1 2.25GiB / worker2 2.75GiB で均衡しており、偏りは requests の値だけだった。sandbox を分散すると基盤が集まる worker1 が重くなる。2VM 化後に再評価する。
- metrics-server を `manifests/infrastructure/` に追加し、bootstrap Application を登録する。kubelet serving 証明書は [kubelet-serving-certificates](../manifests/infrastructure/security/kubelet-serving-certificates/) で発行済みのため、`--kubelet-insecure-tls` は使わない。[observability.md](../docs/observability.md) の記述を更新する。
- ホストの kubectl を 1.36 系へ更新する（現在 1.33、skew 警告）。

## 6. Phase 1: 基盤の軽量化とデータ配置

### 6.1 Harbor Trivy の無効化

- [values.yaml](../manifests/platform/harbor/values.yaml) の `trivy.enabled` を `false` にする。
- `harbor-trivy-0` StatefulSet 削除後に残る PVC `data-harbor-trivy-0` を削除し、`archiveOnDelete` で残る NFS 上の archived ディレクトリも削除する。
- スキャンは [harbor-oidc.yml.tmpl](../automation/scripts/github-actions/harbor-oidc.yml.tmpl) に `aquasecurity/trivy-action` を追加し、`exit-code: 0`（報告のみ）で開始する。Phase 3 の共通 workflow へ引き継ぎ、ゲート化は結果を見て判断する。
- Harbor UI の脆弱性表示は失われる。[harbor-retention.md](../docs/harbor-retention.md) にその旨を記録する。

### 6.2 ArgoCD

2026-11-11 に定期確認間隔の延長を実施した（実施記録は 12 節）。

- `argocd-cm` の `timeout.reconciliation` を 300s にする。Git webhook は未設定で、ArgoCD は Git の変更もこの定期確認で検出する。
  そのため Image Updater の書き戻しから配布開始まで最大約5分かかる（変更前は約3分）。Harbor maintenance の最大10分と合わせ、CI 公開から配布まで最大約15分。
- `argocd-cmd-params-cm` に `controller.log.level: info` を明示する。Git 外の `kubectl patch` で debug が残り、application-controller のログの8割を占めていた。
- applicationset-controller の停止は見送り。ApplicationSet は0件だが、使用量は 25Mi / 3m で効果がない。
- dex（GitHub SSO）と notifications（Slack webhook）は使用中のため残す。
- 将来、配布遅延を縮めたい場合は GitHub webhook を検討する。ArgoCD は内部 URL のみで公開しているため、GitHub から届く経路が別途必要。

### 6.3 Image Updater 由来 CI の抑制

[sandbox-images.yaml](../manifests/platform/ci-cd/image-updater/sandbox-images.yaml) は `method: git`、`branch: main` で
`manifests/apps/<app>/kustomization.yaml` の digest だけを書き換える。この commit が [ci-deploy-validate.yml](../.github/workflows/ci-deploy-validate.yml) を毎回起動している。

- ImageUpdater CRD（v1.3.1）に commit message のテンプレート項目があれば `[skip ci]` を付ける。実装時に CRD のフィールド名を確認する。
- 上記が使えない場合は、`github.event.head_commit.message` が `build: automatic update of` で始まる push では `kustomize build` と kubeconform のみ実行し、PostgreSQL runtime など重い job を skip する。
- git write-back 自体は監査性のため維持する。`method: argocd` へは変えない。

### 6.4 バックアップ強化（取り下げ）

2026-11-11 に検討し、実施しないと判断した。既存の pgBackRest repo1（NFS、週次 full / 日次 diff、full 3世代）と、
[upgrade-precheck.sh](../automation/scripts/upgrade/upgrade-precheck.sh) の更新前 etcd snapshot はそのまま維持する。

検討時に確認したデータの内訳（2026-11-11 実測）:

| データ | 大きさ | 判断 |
|---|---|---|
| PostgreSQL（media / hitomi / api_hub / camera / registry） | DB計約92MB、pgBackRest full 約10MB | 失っても業務上の損失は小さい。repo1 のみとする |
| Nature Remo `buttons.json` | 数KB（登録0件） | 対象なし |
| RustFS（`pdfs` 297GB、`home-camera-recordings` 30GB） | 約327GB | 全 bucket が30日 lifecycle の再取得用データ |
| Harbor イメージ | 2.4GiB | 各アプリリポジトリから再ビルドできる |
| 監視（VictoriaMetrics、Loki、Grafana） | 約0.3GiB | 失ってよい。ダッシュボードは Git から配布 |
| etcd | snapshot 約100MB | ホスト喪失時は `make all` と GitOps で再構築する。秘密値は Pulumi ESC にある |

OCI Always Free の Object Storage は全層合計 20GB で容量は足りるが、API リクエストは月5万回が上限。
`archive_timeout` が PGO 既定の1分で WAL が1日約1,150本（`pg_stat_archiver` の実測）切り替わるため、
pgBackRest の WAL 送信だけで上限を超える見込みだった。必要性が低いことと合わせて取り下げた。

再検討する場合の前提:

- Nature Remo に信号を登録したら、`buttons.json` は再取得できない唯一のデータになる。その時点で日次複製を再検討する。
- オフサイトに repo2 を使う場合は `archive_timeout` を15分程度に延ばし、API リクエスト数を見積もり直す。
- etcd を戻さずに再構築すると、Tailscale operator のデバイスが新しく作られ、古いデバイスが Tailnet に残る。再構築後に管理画面で削除する。

### 6.5 PostgreSQL を local-path へ移行

pgdata は 22MB で、NFS export の `sync` と HDD の fsync 遅延を受ける意味がない。

- PGO の instance set 追加で移行する。`instances` に `main-local`（`storageClassName: local-path`）を追加し、streaming replication が追いついたら `patronictl switchover` で primary を移し、旧 `main` を削除する。
- local-path はノード固定になる。Phase 2 後の worker2 に置く（CP には置かない）。
- 旧 PVC は `nfs-postgresql` の Retain で残る。復元試験が成功した後に削除する。
- `make phase2` は VM と local-path データを失う。[bootstrap.md](../docs/bootstrap.md) と [shared-postgresql.md](../docs/shared-postgresql.md) に「再構築前に repo1 の full backup を確認する」手順を追加する。

## 7. Phase 2: 2VM 化（B案）

### 7.1 Terraform の `for_each` 化（2-a）

[main.tf](../automation/infrastructure/main.tf) は worker を `count = 2`、VM 全体を `count = 3` で定義しており、count を減らすと index の大きい worker2 が消える。
worker2 には local-path の PV（loki-data、grafana-data、alloy-data）と全 sandbox Pod があるため、残すのは worker2 でなければならない。

- worker の volume / cloud-init / domain を `for_each = var.workers`（key は `k8s-worker1`、`k8s-worker2`）へ変更し、`moved` ブロックで state を移す。
- `terraform plan` が No changes になることを完了条件とする。この PR では VM を変更しない。
- `variables.tf` の `worker_memory` / `control_plane_memory` を map 化し、ノード別に指定できるようにする。

### 7.2 切替手順（2-b）

repo1 の直近 full backup 成功を確認してから、次の順で実施する。

1. `kubectl drain k8s-worker1 --ignore-daemonsets --delete-emptydir-data`。Pod が worker2 で Ready になることを確認する。
2. `kubectl delete node k8s-worker1`。
3. `var.workers` から worker1 を外し `terraform apply`。plan が worker1 の destroy のみであることを確認する。
4. CP の `control_plane_memory` を 5120、worker2 の memory を 8192 にして `terraform apply`。libvirt provider は memory 変更で VM を再起動するため、CP 停止中は API が止まる。`make recover` 相当の Node Ready 待ちで復帰を確認する。
5. `kubectl taint node k8s-control-plane node-role.kubernetes.io/control-plane:NoSchedule-`。kubeadm の再 init で taint が戻らないよう、[main.tf](../automation/infrastructure/main.tf) の kubeadm 設定（`nodeRegistration.taints: []`）にも反映する。
6. CP の kubelet 設定に `systemReserved`（memory 512Mi、cpu 200m）と `evictionHard`（memory.available 300Mi）を入れ、control-plane 静的 Pod を保護する。
7. `topologySpreadConstraints` により CP と worker2 で Pod が分散することを確認する。ArgoCD を CP 側へ寄せたい場合は `nodeAffinity`（preferred）を argocd-config に追加する。

### 7.3 確認と記録

- `kubectl get nodes` が 2 台 Ready、全 Application Synced / Healthy。
- ホスト `free -h` の available 2GiB 以上、swap 使用 256MiB 未満、`/proc/pressure/cpu` の some avg300 が 10% 未満。
- [architecture.md](../docs/architecture.md)、[host-capacity.md](../docs/host-capacity.md)、[cluster-contract.yaml](../manifests/contracts/home-lab/cluster-contract.yaml)、
  `settings.toml.example` の `worker_1_ip` と `containerd_canary_node` を更新する。[version-audit.py](../automation/scripts/ci/version-audit.py) と [gitops-check.py](../automation/scripts/ci/gitops-check.py) のノード名参照を確認する。
- [OCI単一クラスタ計画](oci-single-cluster-plan.md) の「現状」表を 2VM 構成へ改訂する。A1 確保後は CP を OCI へ移し、自宅 CP VM は worker へ転用する方針は変えない。

切戻し: `var.workers` に worker1 を戻して `terraform apply`、cloud-init の join で再参加する。local-path データは worker2 にあるため影響しない。

## 8. Phase 3: CI・リポジトリ統合

### 8.1 共通 publish workflow

- home-lab-k8s（public）に `.github/workflows/publish-harbor.yml` を `on: workflow_call` で置く。inputs は `image_name`、`context`、`dockerfile`、`test_workflow` 相当のフラグ、secrets は `inherit`。
- 各アプリリポジトリの workflow は、自前のテスト job と `uses: home-lab-ksera/home-lab-k8s/.github/workflows/publish-harbor.yml@main` の呼び出しだけにする。`id-token: write` は呼び出し側で付与する。
- [add-runner.sh](../automation/scripts/github-actions/add-runner.sh) のテンプレートは呼び出し側の雛形へ縮小する。AGENTS.md の「生成 workflow は git 非追跡」の記述は実態（各リポジトリで commit 済み）に合わせて修正する。
- Phase 1 で各テンプレートに入れた trivy-action は共通 workflow へ移す。
- 移行順: nature-remo（最小）→ blog / cookLog → api-hub / home-camera → hitomi / hitomi-pdf-batch → RSSHub-private。

### 8.2 依存更新の共通化

- home-lab-k8s に Renovate preset（`default.json`）を置き、各リポジトリの `renovate.json` は `"extends": ["github>home-lab-ksera/home-lab-k8s"]` のみとする。
- [renovate.yml](../.github/workflows/renovate.yml) の自己ホスト実行を org 配下の 9 リポジトリへ広げる。GitHub App token の権限を確認する。
- 初回に解消する既知の遅れ: blog / cookLog の Node 20（2026-04 EOL）と nginx 1.27、nature-remo の `go 1.23` 宣言と Go 1.26 builder、hitomi の `rust-toolchain 1.88.0` と Dockerfile `rust:1.99.1`（テストとリリースで別コンパイラ）、各リポジトリの `actions/checkout@v4`。
- RSSHub-private は dependabot の npm 更新を止め、週次で upstream `master` を取り込む workflow に置き換える。自作 route は [CUSTOM_RSS.md](../../RSSHub-private/CUSTOM_RSS.md) の数件のみで、依存更新は upstream に任せる。

### 8.3 branch と保護

- 未マージの `ci/tailscale-oidc`（RSSHub-private）、`fix/expected-upstream-responses`（hitomi-pdf-batch）、cookLog の未 commit テストをマージまたは破棄し、判断を PR に残す。
- hitomi 17、api-hub 13、hitomi-pdf-batch 13 の branch を棚卸しし、マージ済みと放置を削除する。
- 9 リポジトリの main に ruleset（PR 必須、CI 必須、force push 禁止）を設定する。[台帳CI計画](contract-ci-security-plan.md) の未設定項目と同時に行う。

### 8.4 ドキュメント

- docs/ は現行構成のみを記述する。`automation/*/README.md` の旧手順は削除し、必要な内容は docs/ へ移す。AGENTS.md の該当記述も更新する。
- api-hub の `JSONL_LOGGING_BENEFITS.md`、`LOGGING_OPERATIONS_GUIDE.md` は `docs/` へ移す。hitomi の `docker-*.sh` は役割を一覧化し、重複を統合する。
- 空の `production` / `staging` / `tools` namespace は削除候補として記録する。`tools` の NetworkPolicy 2 件の所有定義を確認してから判断する。改名は構造改革で扱う。

## 9. 受け入れ基準

| 項目 | 基準 | 確認方法 |
|---|---|---|
| ホスト容量 | available 2GiB 以上、swap 使用 256MiB 未満が 7 日継続 | ホスト `free`、VictoriaMetrics の node metrics |
| CPU | ホスト `/proc/pressure/cpu` some avg300 10% 未満 | ホストで読み取り |
| Probe | readiness / startup 失敗イベントが 7 日で 0 | `kubectl get events -A --field-selector type=Warning` |
| Metrics API | `kubectl top nodes` / `pods` が応答 | 管理端末 |
| 2VM | Node 2 台 Ready、48 Application Healthy、CP で workload が稼働 | `kubectl get nodes,applications -A` |
| バックアップ | local-path 移行後も repo1 の full / diff が週次・日次で成功 | `pgbackrest info`、`PostgreSQLBackupStale` が発火しない |
| CI | Deploy Process Validation の月間 run が半減、publish workflow が共通化 | `gh run list` |
| 依存更新 | 9 リポジトリで Renovate PR が作成され、Node 20 / nginx 1.27 / Go / Rust の乖離が解消 | 各リポジトリの PR |

## 10. 実装時の仕様確認先

- libvirt memballoon と `virsh setmem --live`: libvirt ドキュメント。`freePageReporting` が有効でもページキャッシュは返却されない。
- terraform-provider-libvirt の `memory` 変更時の挙動（再起動）と `for_each` / `moved`。
- PGO v6 の instance set 追加・switchover。
- ArgoCD Image Updater v1.3.1 の ImageUpdater CRD（commit message / write-back）。
- metrics-server と kubelet serving 証明書の要件。
- GitHub reusable workflow の `secrets: inherit`、`id-token` の継承、public リポジトリから private リポジトリへの呼び出し制約。
- Renovate の shareable config preset と自己ホスト実行時の `repositories` / `autodiscover`。

## 11. Phase 0 実施記録（2026-11-11）

| 項目 | 結果 |
|---|---|
| balloon | `virsh setmem --live` で worker1 6144→4608MiB、worker2 6144→5120MiB。CP は据え置き。VM 再起動で宣言値へ戻る |
| 効果 | ホスト available 2.3→4.2GiB、free 0.2→2.1GiB。swap 使用は 3.45→3.07GiB で、以後は参照時に戻る |
| VM 内 | worker1 available 2.1GiB、worker2 2.2GiB、memory PSI some avg300 0.05 以下 |
| Probe 失敗 | 縮小後は CronJob 起動時の startup 2件のみ。Probe 設定は変更しない |
| Harbor maintenance | `*/10` へ変更（上記の理由） |
| metrics-server | chart 3.14.0（0.8.1）を wave 3 の infrastructure Application として追加。`--kubelet-insecure-tls` なし |
| oci-always-free Job | Git 管理外の手動 Job。Autonomous DB 2台の STOPPED（Always Free の無操作自動停止）で Safety stop。削除せず所有者判断とする |
| 未実施 | ホスト不要サービス停止と kubectl 更新は `sudo` の対話認証が必要なため、管理者がホストで実行する |

balloon 後も kubelet の allocatable は宣言値（worker 各 5.7GiB）のままで、scheduler は縮小を認識しない。
worker2 の requests 合計 4.7GiB は実容量 4.9GiB に近いため、Phase 2 まで新しい常駐 workload を追加しない。

## 12. ArgoCD 定期確認間隔の実施記録（2026-11-11）

変更前の実測は application-controller 141m / 404Mi、repo-server 13m / 131Mi。
application-controller のログは10分で6,808行（debug 5,451、info 1,357）、約1.4MB だった。
反映後は再起動から約10分後に同じ方法で測った。

| 項目 | 変更前 | 変更後 |
|---|---|---|
| controller ログ（10分） | 6,808行 / 約1.4MB（debug 5,451） | 634行 / 約155KB（全て info） |
| controller CPU | 67m（変更前1時間の平均） | 46m（再起動後8分の平均） |
| controller メモリ | 404Mi | 300Mi（再起動直後のため今後増える可能性あり） |
| repo-server CPU | 7m（変更前1時間の平均） | 25m（再起動後8分の平均、キャッシュ再構築を含むため参考値） |

全 Application は Synced / Healthy のまま。CPU は短時間の値のため、1週間後に1時間平均で再確認する。
