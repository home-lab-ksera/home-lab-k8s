# 障害対応

まず [日常運用の確認](operations.md) で対象クラスタと障害範囲を確定します。
`automation/run.log` はローカル操作のログで、ArgoCD の同期・Pod のログは Kubernetes 側で確認します。
復旧を急ぐ場合も、既存環境を削除する `make phase2` / `make all` を再試行の代わりに使わないでください。

| 症状 | 調べる順序 |
|---|---|
| API に接続できない | kubeconfig/context → libvirt VM → SSH → kubelet/containerd → API server |
| Node NotReady | Node の Conditions / Events → kubelet → containerd → Flannel |
| Pending | Pod Events → requests / node selector / taint → PVC / StorageClass / NFS |
| ImagePullBackOff | image 名と tag → pull Secret → Harbor / DNS / TLS → registry に対象イメージがあるか |
| CrashLoopBackOff | 現在と previous のログ → probe →設定・Secret →依存サービス |
| ArgoCD 同期失敗 | Application Conditions / operationState → source の描画 → CRD / admission →リソースの所有重複 |
| ESO 未 Ready | Store → token と参照 environment → remoteRef → target Secret の creationPolicy |
| URL に接続できない | DNS →経路 →証明書 → Gateway / Route condition → Service / EndpointSlice → Pod / NetworkPolicy |

## Node / Pod

```bash
kubectl describe node <node-name>
ssh k8suser@<node-ip> 'sudo systemctl status kubelet containerd'
ssh k8suser@<node-ip> 'sudo journalctl -u kubelet -n 200 --no-pager'
kubectl -n <namespace> describe pod <pod-name>
kubectl -n <namespace> logs <pod-name> -c <container-name> --tail=100
kubectl -n <namespace> logs <pod-name> -c <container-name> --previous --tail=100
```

initContainer が待機中なら、そのコンテナのログを指定します。Harbor の DB 待機は下記を参照してください。

## ArgoCD / Secret

```bash
kubectl -n argocd describe application <app-name>
kubectl describe clustersecretstore pulumi-esc-store
kubectl -n <namespace> describe externalsecret <name>
```

ローカル Git source は `HEAD`、外部 Helm source は chart の版番号です。
Secret の本文をログに出す必要はありません。ArgoCD の Merge 対象 Secret は upstream の作成も確認します。
詳しくは [GitOps](gitops.md) と [Secret 管理](secrets.md) を参照してください。

## Gateway / TLS / HTTP 413

```bash
kubectl -n nginx-gateway get gateway,svc,certificate
kubectl -n <namespace> describe httproute <route-name>
kubectl -n <namespace> get svc,endpointslices
kubectl -n kube-system logs daemonset/harbor-node-hosts --since=10m
kubectl get clientsettingspolicies -A
```

Harbor を含む内部 URL は Tailnet / LAN の経路と Split DNS を先に確認します。
HTTPS の調査では hostname と証明書を照合し、`curl -k` の成功を TLS 検証成功として扱いません。
413 は api-hub / Harbor の ClientSettingsPolicy の Accepted / Programmed と実際の NGINX 設定を確認します。
NGF / Gateway API の更新手順は [アプリ更新](application-auto-updates.md) を参照してください。

## Harbor / PostgreSQL

```bash
kubectl -n harbor get pods
kubectl -n harbor logs deployment/harbor-core --all-containers=true --tail=100
kubectl -n database get postgrescluster,pods,jobs,pvc
kubectl -n database get statefulset,pods,jobs
```

`Waiting for PostgreSQL credentials, TLS, and verified Harbor restore` は
資格情報・TLS・復元マーカーのいずれかが未準備であることを示します。
待機を除去するためだけにマーカーを書き込まず、[復旧手順](shared-postgresql.md#再起動と再構築) で復元内容を確認します。
Harbor の内部認証エラーでは [固定 Secret](secrets.md#harbor-内部認証情報) と各プロセスへの反映を確認します。

## ストレージ

```bash
kubectl get storageclass,pv
kubectl -n <namespace> describe pvc <pvc-name>
kubectl -n nfs-storage get pods
kubectl -n local-path-storage get pods
```

NFS 到達性・export・権限・実空き容量、local-path の配置ノードを確認します。
PVC の削除・再作成を一般的な復旧方法にしないでください。旧 PV と実データの対応を先に保存します。
