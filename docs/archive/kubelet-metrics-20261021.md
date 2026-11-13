# kubelet serving 証明書への移行（2026-10-21）

[kubelet metrics の最小権限化](../kubelet-metrics.md) から移した、既存ノードの移行手順と適用記録。新規構築では bootstrap が自動で処理する。

## 既存ノードの証明書移行

新規クラスタでは `automation/infrastructure/main.tf` の kubeadm 設定が
`serverTLSBootstrap: true` を指定する。既存ノードには以下の設定変更が必要。
既存クラスタを移行するために Terraform のリソースを再作成してはいけない。

既存クラスタは次の順で移行する。ノードの変更は1台ずつ行い、その都度 Ready と証明書を確認する。

1. `kube-system/kubelet-config` ConfigMap をバックアップし、`data.kubelet` 内の
   `KubeletConfiguration` に `serverTLSBootstrap: true` を設定する。
   `tlsCertFile` / `tlsPrivateKeyFile` に旧証明書への固定参照があれば削除する。
   kubeadm upgrade / join 時にもこの構成が使われるよう、クラスタ側も更新する。
2. 対象ノードの `/var/lib/kubelet/config.yaml` をバックアップし、同じ変更を行う。
   systemd unit / drop-in / kubelet の起動引数にも `--tls-cert-file` / `--tls-private-key-file`
   の固定指定がないことを確認する。クライアント認証用 kubeconfig や鍵は変更・削除しない。
3. `sudo systemctl restart kubelet` を実行する。
4. 自動承認処理を初回導入する。旧設定のノードが残る間は、承認処理の導入後に全台検証が
   タイムアウトして終了してよい。次のノードへ進む前に対象ノードの証明書とReadyを確認する。

   ```bash
   make kubelet-certificates
   ```

   正常なCSRは自動承認される。Pending が残った場合は以下で内容と承認処理のログを確認する。
   手動承認は自動承認処理を使わない場合の代替手順。検証の不一致を回避するために承認しない。

   ```bash
   kubectl -n kube-system logs deployment/kubelet-serving-approver -c approver
   ```

```bash
kubectl get csr
CSR=<対象のCSR名>
kubectl get csr "$CSR" -o yaml
kubectl get csr "$CSR" -o jsonpath='{.spec.request}' \
  | base64 -d | openssl req -noout -text
kubectl get nodes -o wide
```

確認する内容:

- signerName は `kubernetes.io/kubelet-serving`。
- requestor と CSR の CN は `system:node:<対象ノード名>`、Organization は `system:nodes`。
- SAN は対象ノードの DNS 名 / InternalIP と一致し、他ノードの名前・IP を含まない。
- usages はサーバー認証用で、クライアント認証用ではない。

確認済みの CSR だけを個別に承認する。一括承認や未検証 CSR の自動承認はしない。

```bash
kubectl certificate approve "$CSR"
kubectl wait --for=condition=Ready node/<対象ノード名> --timeout=120s
```

5. 実際に配信された証明書を CA と IP の両方で検証する。以下は管理端末で行う
   TLS の事前確認であり、Pod からの通信確認は別途必要。

```bash
kubectl -n observability get configmap kube-root-ca.crt \
  -o jsonpath='{.data.ca\.crt}' > /tmp/kubelet-metrics-ca.crt
NODE_IP=192.168.122.10 # .11 / .12 でも実施
openssl s_client -connect "${NODE_IP}:10250" \
  -CAfile /tmp/kubelet-metrics-ca.crt -verify_ip "$NODE_IP" \
  -verify_return_error </dev/null
```

`Verify return code: 0 (ok)` を確認する。旧証明書のままなら固定パス・起動引数・kubelet のログを調べる。
必要に応じて正しい設定で再起動し、証明書配信を再確認する。
既存の API server proxy 経由の収集も維持されていることを確認してから次のノードへ進む。

## 既存クラスタへの適用記録（2026-10-21）

22:14〜22:18 JST に worker1 → worker2 → control-plane の順で移行した。
クラスタ共通 `kubelet-config` と各ノードの設定へ `serverTLSBootstrap: true` を追加し、
kubelet を1台ずつ再起動。固定証明書参照は全台とも存在しなかった。
CSR は台帳照合付き承認処理が自動承認し、3件とも Approved,Issued を確認した。

| Node | CSR | ノード内の設定バックアップ |
|---|---|---|
| k8s-worker1 | csr-6x4c5 | `/var/lib/kubelet/config.yaml.before-serving-20261021T131424Z` |
| k8s-worker2 | csr-vqttn | `/var/lib/kubelet/config.yaml.before-serving-20261021T131606Z` |
| k8s-control-plane | csr-m4f4d | `/var/lib/kubelet/config.yaml.before-serving-20261021T131749Z` |

全台 Ready、クラスタCA・InternalIP SAN・有効期限の検証成功、
Podからの `kubelet_serving_certificate_valid` が全台 `1`、既存監視の3台×2ジョブが
新しいscrapeでもすべて `up` であることを確認した。証明書の有効期限は全台2027-10-21 UTC。
再起動〜発行完了の間には一時的なscrape失敗があり、その後の回復を各ノードで確認した。
SSH鍵が使用できなかったため、一時的な保守Pod経由で設定変更し、作業後は全Podを削除した。
クラスタ共通設定のバックアップと検証記録は、実行端末の
`~/.local/state/k8s_myHome/kubelet-cert-migration-20261021/` に保存している。

承認処理の Application は Healthy / Synced。PR未マージのため、実クラスタだけ
`targetRevision: 9ec0fd985d58ffd0075cd7b85ddf999baf29cb77` に固定してArgoCD管理へ移した。
Git上のbootstrap定義は `HEAD` を維持しており、PRマージ後にrootが採用して通常管理へ戻す。
この作業ではPRをマージしていない。監視収集経路とRBAC、新しいアラートルールの切り替えは
PRマージ後に行う。証明書整備というマージ前提は満たしたが、直接scrapeの確認は未実施。

参考:

- [kubelet の認証・認可](https://kubernetes.io/docs/reference/access-authn-authz/kubelet-authn-authz/)
- [kubeadm の serving 証明書設定](https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/#enabling-signed-kubelet-serving-certificates)
