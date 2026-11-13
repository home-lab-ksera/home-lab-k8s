# kubelet metrics の最小権限化

VictoriaMetrics は kubelet の `/metrics` と `/metrics/resource` を直接収集する。
`nodes/metrics` の `get` はこの収集に必要な権限で、`nodes/proxy` は不要になる。
API server の `/api/v1/nodes/<node>/proxy/...` に接続する構成では、この削減はできない。

## マージ・初回同期の前提

**既存クラスタでは、以下の証明書準備を全ノードで完了してから収集設定をマージする。**
ArgoCD の自動同期で ConfigMap と RBAC が反映されるため、先にマージすると収集が失敗する。
この手順はノード設定変更を含む。PR の作成・CI の実行だけでは実施されない。

- kubelet の認証は Webhook 有効、匿名アクセス無効、認可は Webhook とする。
- 各ノードの 10250 番で、クラスタ CA が署名し、接続先 InternalIP を SAN に持つ証明書を配信する。
- VictoriaMetrics Pod から各ノードの TCP 10250 へ到達できること。
- この repo の IPv4 構成を対象とする。IPv6 化時は scrape の `__address__` に角括弧を付ける変更も必要。

2026-10-20 の調査では全3ノードが認証・認可の条件を満たしたが、証明書はノード固有の
発行者で DNS SAN のみだった。`insecure_skip_verify` による回避はしない。

## 新規構築と証明書更新の自動化

`make all` / `make phase2` → `make bootstrap` では、手動のCSR承認は不要。

1. Terraform の kubeadm 設定で全ノードの `serverTLSBootstrap: true` を有効化する。
2. bootstrap は root Application の適用前に、Git と同じ
   [kubelet-serving-certificates](../manifests/infrastructure/security/kubelet-serving-certificates/) を初期導入する。
3. 承認処理が30秒間隔で初回・更新用CSRを検査し、条件を満たすものだけを承認する。
4. control-plane から全ノードの Ready、クラスタCA、InternalIP SAN、有効期限（残り1日超）を検証する。
   失敗・タイムアウト時は bootstrap を停止し、root Application を適用しない。
5. 初回導入後は同名の ArgoCD Application が承認処理と設定を管理する。

承認台帳は [nodes.json](../manifests/infrastructure/security/kubelet-serving-certificates/nodes.json)。
`k8s-control-plane=.10`、`k8s-worker2=.12` を明示している。
ノード名・IPを変える場合は、この台帳と ClusterRole の `nodes.resourceNames`、Terraform の構成も変更する。
Node.status だけを信頼せず、要求元、groups、CN、Organization、DNS/IP SAN、usages、CSR署名、
要求有効期間、登録 Node の InternalIP を台帳と照合する。
未知のノード、別ノードのIP、追加のSAN、クライアント証明書用途は承認しない。
不一致CSRは削除・一括拒否せず Pending のまま残し、ログに理由を記録する。

承認処理は標準ライブラリだけを使う小さなGoプログラム。独自registryへの依存を避けるため、
固定版の公式Goイメージを使う initContainer が、ConfigMap のソースをネットワーク不要でビルドする。
実行コンテナは非root・読み取り専用で動き、承認対象 signer は `kubernetes.io/kubelet-serving` のみ。
起動時のビルド用に最大768MiBのメモリが必要。ソース・台帳の変更ではConfigMap名が変わり再起動する。
CA秘密鍵、admin kubeconfig、ノードのホストファイルはPodに渡さない。

証明書の残存14日、TLS検証失敗、承認処理のAPI照合停止を監視する。
承認処理自身もPodから全ノードへTLS接続するため、control-plane側とPod側の到達性を確認できる。
メトリクス収集の認証・認可の確認は、後述の実scrapeで行う。

単独で準備・再確認する場合（既存ノードの設定ファイルやkubelet再起動はこのコマンドでは行わない）:

```bash
make kubelet-certificates
# 既に導入済みの場合、変更なしの検証のみ
bash automation/scripts/kubelet-serving-certificates.sh --verify-only
```

## GitOps で収集と権限を切り替える

全ノードの準備後、収集設定と RBAC をマージし、`observability` Application の同期を確認する。
ConfigMap はボリュームへ伝播後、VictoriaMetrics が30秒間隔で再読込する。
RBAC と ConfigMap の反映には時間差があり、一時的な scrape 失敗があり得る。

- 接続先: ノードの InternalIP:10250。
- 認証: Pod に投影された ServiceAccount token。外部へ取り出さない。
- TLS: クラスタ CA 検証を維持。
- RBAC: `nodes/metrics` は `get` のみ。`nodes` / `pods` は探索用に `get/list/watch`。
- 既存の job 名・`node`・`instance` ラベルと metric フィルターを維持する。
  `instance` は実クラスタの既存時系列に合わせてノード名を明示する（`node` も同じノード名）。
  2026-10-21 に全3ノード・両ジョブで実データのラベルを確認した。

```bash
SA=system:serviceaccount:observability:victoria-metrics
kubectl auth can-i get nodes/metrics --as="$SA"     # yes
kubectl auth can-i get nodes/proxy --as="$SA"       # no
kubectl auth can-i create nodes/proxy --as="$SA"    # no
kubectl -n argocd get application observability
kubectl -n observability port-forward svc/victoria-metrics 18428:8428
```

VMUI / `/targets` とクエリ API で以下を確認する。port-forward は VictoriaMetrics の状態確認に使い、
実際の scrape が Pod → kubelet の TLS・認証・通信の検証になる。

- `up{job=~"kubelet-volume-stats|kubelet-resources"}` が全3ノード・両ジョブで `1`。
  直近の scrape 成功日時と対象 URL が各ノードの 10250 番であることも確認する。
- `kubelet_volume_stats_capacity_bytes` 等が移行前と同じ PVC で更新される。
  元から容量未報告の PVC を新たな障害と誤認しない。
- `node_cpu_usage_seconds_total`、`pod_memory_working_set_bytes` 等が更新される。
- Grafana の CPU・メモリ・容量、週次レポートの dry-run が正常。
- kubelet の認可キャッシュ（調査時は許可5分）が失効した後も収集でき、旧 proxy 権限がないこと。

## 証明書の更新と復旧

serving CSR は標準の controller では自動承認されないため、本PRの承認処理が初回と更新を担当する。
`KubeletServingCertificateExpiring` / `KubeletServingCertificateInvalid` /
`KubeletServingApproverUnavailable` を監視し、異常時は Deployment、Pending CSR、台帳との不一致を調べる。
再実行は未承認CSRだけを対象とし、証明書や秘密鍵の削除は行わない。
既存ノードも設定変更と全台検証を終えてからマージするという前提は変わらない。

証明書移行中に問題が起きた場合は、対象ノードの設定をバックアップへ戻して kubelet を再起動し、
既存経路の収集・Node Ready を確認する。証明書の秘密鍵は削除しない。

GitOps 切り替え後の問題では、まず TLS・到達性・権限・scrape エラーを確認する。
緊急に旧経路へ戻す場合は **scrape 設定と RBAC の両方**を Git revert して同期する。
これは `nodes/proxy` のリスクを再導入する暫定復旧なので、修正後に再び削除する。
署名済み serving 証明書は旧経路でも使えるため、収集の切り戻しで元に戻す必要はない。

既存ノードの移行手順と 2026-10-21 の適用記録は [archive](archive/kubelet-metrics-20261021.md) を参照。
