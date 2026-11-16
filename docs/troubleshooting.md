# 障害対応

まず範囲を確かめます。

```bash
kubectl get nodes
kubectl -n argocd get applications | grep -v 'Synced.*Healthy'
kubectl get pods -A | grep -vE 'Running|Completed'
```

急いでいても、`make phase2` と `make all` は使いません。既存の VM を削除します。

## 症状から探す

| 症状 | 調べる順 |
|---|---|
| 何も届かない（Slack に healthchecks.io の通知） | ホストに SSH できるか → `virsh list --all` → [ホストを再起動したあと](operations.md#ホストを再起動したあと) |
| `kubectl` がつながらない | kubeconfig の context → VM が動いているか → control-plane の kubelet / containerd → API サーバー |
| Node が NotReady | `kubectl describe node` → kubelet → containerd → Flannel |
| 多くの Pod が ContainerCreating のまま | NFS（[下記](#ストレージ)） |
| Pod が Pending | Pod の Events → requests とノードの空き → PVC |
| ImagePullBackOff | イメージ名と digest → Harbor が動いているか → ノードの `/etc/hosts` → pull Secret（[下記](#harbor)） |
| CrashLoopBackOff | `--previous` のログ → 設定と Secret → 依存先（DB、api-hub） |
| Application が OutOfSync / Degraded | `kubectl -n argocd describe application` → 描画できるか → CRD があるか（[下記](#argocd)） |
| ExternalSecret が Ready でない | ClusterSecretStore → ESC の token → `remoteRef` のキー名 |
| URL が開けない | 名前解決 → 経路（tailnet / LAN）→ 証明書 → Gateway と Route → Service と Pod → NetworkPolicy（[下記](#url)） |
| アプリは動いているが仕事が進まない | [アプリのキュー](observability.md#アプリのキュー) |
| control-plane の応答が遅い | ホストの swap と CPU（[容量の考え方](nodes.md#容量の考え方)） |

## Node と Pod

```bash
kubectl describe node <node>
ssh k8suser@<node-ip> 'sudo systemctl status kubelet containerd --no-pager'
ssh k8suser@<node-ip> 'sudo journalctl -u kubelet -n 200 --no-pager'
kubectl -n <namespace> describe pod <pod>
kubectl -n <namespace> logs <pod> -c <container> --tail=100
kubectl -n <namespace> logs <pod> -c <container> --previous --tail=100
```

VM にはホストから入ります（`192.168.122.10` が control-plane、`.12` が worker）。
消えた Pod のログは、`sandbox` のものなら Loki に 7 日残っています。

## ストレージ

```bash
kubectl get pv,pvc -A
kubectl -n <namespace> describe pvc <pvc>
kubectl -n nfs-storage get pods
kubectl -n local-path-storage get pods
ssh ksera@192.168.10.11 'sudo /usr/sbin/exportfs -v; systemctl is-active nfs-kernel-server; df -h /mnt/k8s-storage'
```

- NFS の export がなくなると、RustFS、Harbor、VictoriaMetrics、home-camera が起動できません。Harbor が止まると、
  イメージを持たないノードでアプリも起動できなくなります。`/etc/exports` を直して `sudo /usr/sbin/exportfs -ra` を実行します。
- local-path の Pod が Pending のままなら、PV のあるノード（`k8s-worker2`）が使えるかを見ます。
- PVC を消して作り直すのは最後の手段です。local-path は実データごと消えます。

## ArgoCD

```bash
kubectl -n argocd describe application <app>
kubectl -n argocd get application <app> -o jsonpath='{.status.operationState.message}{"\n"}'
kustomize build --enable-helm manifests/<path> >/dev/null     # 手元で描画できるか
```

- 新しい CRD や PriorityClass を参照する変更は、定義した Application より先に同期されると失敗します。
  定義側の Application を Refresh して同期させれば、自動で再試行されます。
- PreSync の migration Job が失敗すると、そのアプリの同期は止まります。Job（`api-hub-migration`、`hitomi-migration`、`media-migration`）のログを見ます。
- 手で直したリソースは selfHeal で戻ります。直すなら Git を直します。

## URL

```bash
dig @192.168.122.101 <名前>.internal.qroksera.com +short        # 端末側の名前解決
kubectl -n nginx-gateway get gateway,svc,certificate
kubectl -n <namespace> describe httproute <route>
kubectl -n <namespace> get svc,endpointslices
kubectl -n tailscale get pods
```

- tailnet からだけ届かないなら、subnet router の Pod、経路の承認、端末側で経路を受け入れているかを見ます。
- 証明書の問題は `curl -v` で確かめます。`curl -k` で通っても、TLS が正しいことにはなりません。
- 403 は nature-remo の送信元制限、404 は Route のパス制限（api-hub、s3）の可能性があります（[アクセス経路](access.md#url)）。
- Harbor への push で 413 が出るなら、ClientSettingsPolicy が Accepted かを見ます（`kubectl -n harbor get clientsettingspolicies`）。

## Harbor

```bash
kubectl -n harbor get pods
kubectl -n harbor logs deployment/harbor-core --all-containers --tail=100
kubectl -n kube-system logs daemonset/harbor-node-hosts --since=10m
ssh k8suser@192.168.122.12 'grep harbor /etc/hosts; sudo crictl pull harbor.internal.qroksera.com/sandbox/blog:latest'
```

- core が `Waiting for PostgreSQL credentials, TLS, and verified Harbor restore` で止まっているなら、DB に接続できないか、
  DB が空です（[Harbor の起動ゲート](postgresql.md#harbor-の起動ゲート)）。印を足して通す前に、DB の中身を確かめます。
- push が `denied` や quota のエラーで失敗するなら、project `sandbox` の quota（4GiB）を見ます。
- 内部認証のエラーは [Harbor の内部認証](secrets.md#harbor-の内部認証) の Secret の組が揃っているかを見ます。

## PostgreSQL

```bash
kubectl -n database get statefulset,pods,pvc,jobs
kubectl -n database logs postgresql-0 --tail=100
kubectl -n database exec postgresql-0 -- psql -U postgres -c 'select usename, count(*) from pg_stat_activity group by 1'
```

- `too many connections` は [接続数](postgresql.md#接続数) の割り当てを見ます。
- 証明書のエラー（`certificate has expired`）は、サーバー証明書の更新後に PostgreSQL が再起動されていない可能性があります（[証明書](postgresql.md#証明書)）。
- データが壊れた、消えた場合は [復元](postgresql.md#復元) です。

## Secret

```bash
kubectl describe clustersecretstore pulumi-esc-store
kubectl -n <namespace> describe externalsecret <name>
kubectl -n external-secrets-system logs deployment/external-secrets-operator --tail=50
```

Store が Ready でなければ、`external-secrets-system/pulumi-esc-token` の token が失効していないかを確かめます。
Secret の中身をログや端末に出す必要はありません。
