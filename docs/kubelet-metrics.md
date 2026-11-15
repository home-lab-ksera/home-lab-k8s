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

## 証明書の承認（手動）

serving CSR は標準の controller では自動承認されません。ノードは 2 台で証明書の有効期間は 1 年のため、
常駐の承認処理は置かず、初回は bootstrap が、更新時は管理者が承認します（自作の approver は 2026-11-16 に撤去）。

1. Terraform の kubeadm 設定で全ノードの `serverTLSBootstrap: true` を有効化する。
2. `make bootstrap` は root Application の適用前に、[台帳](../automation/scripts/kubelet-serving-nodes.json)の
   ノード自身（`system:node:<名前>`）が出した未承認の `kubernetes.io/kubelet-serving` CSR だけを承認する。
3. control-plane から全ノードの Ready、クラスタ CA、InternalIP SAN、有効期限（残り 1 日超）を検証する。
   失敗・タイムアウト時は bootstrap を停止し、root Application を適用しない。

ノード名・IP を変える場合は、台帳と Terraform の構成を変更します。

```bash
make kubelet-certificates                                               # 未承認分を承認して検証
bash automation/scripts/kubelet-serving-certificates.sh --verify-only   # 検証のみ
```

### 更新時

kubelet は有効期間の残りが約 2〜3 割になると新しい CSR を出し、承認されるまで出し続けます。
`KubeletServingCSRPending`（未承認が 1 時間継続）が届いたら、要求元を確認して承認します。

```bash
kubectl get csr | grep Pending
kubectl get csr <名前> -o jsonpath='{.spec.username}{"\n"}{.spec.signerName}{"\n"}'   # system:node:<ノード名> / kubernetes.io/kubelet-serving
kubectl certificate approve <名前>
```

承認しないまま期限が切れると、VictoriaMetrics と metrics-server の kubelet への接続が TLS 検証で失敗します
（`KubeletVolumeScrapeUnavailable` で検知）。Pod の実行には影響しません。

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

## 復旧

証明書移行中に問題が起きた場合は、対象ノードの設定をバックアップへ戻して kubelet を再起動し、
既存経路の収集・Node Ready を確認する。証明書の秘密鍵は削除しない。

GitOps 切り替え後の問題では、まず TLS・到達性・権限・scrape エラーを確認する。
緊急に旧経路へ戻す場合は **scrape 設定と RBAC の両方**を Git revert して同期する。
これは `nodes/proxy` のリスクを再導入する暫定復旧なので、修正後に再び削除する。
署名済み serving 証明書は旧経路でも使えるため、収集の切り戻しで元に戻す必要はない。
