# Secret 保存時暗号化と API 監査

新規構築では `automation/infrastructure/main.tf` が Control Plane 上で
`apiserver-security/configure.py prepare` を実行し、kubeadm v1beta4 設定へ引数と hostPath を追加します。
Secret は AES-CBC で暗号化し、既存平文を読み込むために identity を最後に置きます。
鍵は `/etc/kubernetes/security/encryption-config.yaml`（root:root、0600）に生成します。
Git、Terraform state、ConfigMap、実行ログに鍵を入れません。再実行では既存鍵を保持し、破損時は停止します。

監査は Metadata（利用者、操作、対象、結果など）を記録し、本文や Secret の値を記録しません。
すべての変更（create / update / patch / delete）、人の操作、Secret へのアクセスを記録します。
次の機械的な要求は記録しません: health 確認と metrics、leader election の lease、
ServiceAccount・kubelet・control-plane コンポーネントによる Secret 以外の読み取り（get / list / watch）。
全要求を記録していた時期は約 107 MB / 時で、保持が約 10 時間しかありませんでした。
`/var/log/kubernetes/audit/audit.log` を最大 100 MB × 10 世代、最大 30 日でローテーションします。

稼働中のクラスタでポリシーを変えるときは、Control Plane で `sudo python3 configure.py prepare` を実行して
`audit-policy.yaml` を書き直し、API Server を再起動します（ポリシーは起動時にしか読まれません）。
変更前のファイルを退避し、再起動後に `/livez` が戻らなければ元に戻します。
監査はローカル保存のため、ノード侵害時の改ざん耐性や外部保管は別途必要です。

## 既存クラスタの移行

Terraform の再適用では既存の provisioner は再実行されません。VM の再作成で対応しないでください。
以下は **Control Plane 上**で実行する手順です。単一 Control Plane では API Server の短時間停止が発生します。
事前に正常な etcd snapshot と `/etc/kubernetes` を安全な場所へバックアップし、復元手段を確認します。
暗号化開始後は snapshot だけでは復元できません。鍵を snapshot と別のアクセス制御された場所へ保管します。
複数 Control Plane では全 API Server に同一の鍵・provider 順序を配布してから順次切り替えます。
このリポジトリの自動生成は単一 Control Plane 用です。

1. リポジトリの `automation/infrastructure/apiserver-security/configure.py` を Control Plane にコピーします。
   以下は同ファイルのあるディレクトリから実行します。

   ```bash
   sudo apt-get install -y python3-yaml
   sudo python3 configure.py prepare
   sudo install -d -m 0700 /etc/kubernetes/security-backup
   sudo cp -p /etc/kubernetes/manifests/kube-apiserver.yaml /etc/kubernetes/security-backup/kube-apiserver.yaml
   sudo python3 configure.py manifest \
     --input /etc/kubernetes/manifests/kube-apiserver.yaml \
     --output /etc/kubernetes/kube-apiserver.secure.yaml
   sudo diff -u /etc/kubernetes/manifests/kube-apiserver.yaml /etc/kubernetes/kube-apiserver.secure.yaml
   ```

   diff の終了コード 1 は差分ありを示します。引数と mount 以外の意味的な変更がないことを確認します。
   既存の別パスに暗号化設定がある環境では、新しい鍵へ切り替えず既存鍵を移行してください。
   鍵ファイルを安全にバックアップしてから次へ進みます。バックアップを manifests 内に置いてはいけません。

2. 同一 filesystem 内の rename で static Pod manifest を切り替えます。

   ```bash
   sudo mv /etc/kubernetes/kube-apiserver.secure.yaml /etc/kubernetes/manifests/kube-apiserver.yaml
   sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get --raw=/readyz
   ```

   kubelet の再作成を待ち、readyz が成功するまで確認します。失敗時は `crictl logs` と kubelet journal を確認します。
   保存前の段階ならバックアップ manifest を戻せます。暗号化済み Secret が存在する段階では、
   暗号化引数を外すだけの rollback はできません。鍵を維持して原因を修正してください。

3. kubeadm の永続設定にも反映します（upgrade による引数消失を防ぐ）。

   ```bash
   sudo sh -c 'umask 077; kubectl --kubeconfig=/etc/kubernetes/admin.conf -n kube-system get cm kubeadm-config -o jsonpath="{.data.ClusterConfiguration}" > /etc/kubernetes/cluster-before.yaml'
   sudo python3 configure.py kubeadm --input /etc/kubernetes/cluster-before.yaml --output /etc/kubernetes/cluster-secure.yaml
   sudo kubeadm init phase upload-config kubeadm --config /etc/kubernetes/cluster-secure.yaml --kubeconfig /etc/kubernetes/admin.conf
   ```

   v1beta3 以前の設定は先に `kubeadm config migrate` で v1beta4 に変換します。

4. 全 API Server の更新後、既存 Secret を再保存します。新規書込だけでは既存データは暗号化されません。
   以下は Secret 値を端末や一時ファイルへ出力しません。競合で失敗した場合は最新データで再実行します。

   ```bash
   set -o pipefail
   sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf get secrets --all-namespaces -o json |
     sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf replace -f -
   ```

5. 既存 Secret の読出し、アプリの正常動作、監査ログへの `get` 操作の記録を確認します。
   etcd の **全 Secret キー** の保存値が `k8s:enc:aescbc:v1:` prefix を持つことも、
   root 権限で `etcdctl` を使い確認します。値本体を端末やログへ出力しないでください。
   kubectl の出力は復号後なので暗号化の証明になりません。旧 snapshot には平文が残るため、
   保持期間と廃棄を管理します。identity の削除は全件検証後の別作業とします。

## 鍵ローテーション

既存鍵を削除・再生成しないでください。全 API Server へ新鍵を復号可能な状態で配布し、
新鍵を先頭へ移して再起動、Secret の再保存、全保存値の key name 確認を経て旧鍵を退役します。
旧バックアップの復元に必要な鍵はバックアップの保持期間中保管します。

参考: [Kubernetes の保存時暗号化](https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/)、
[監査](https://kubernetes.io/docs/tasks/debug/debug-cluster/audit/)。
