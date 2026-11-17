# 構築と再構築

入口は [Makefile](../Makefile)、実行順は [run.sh](../automation/scripts/run.sh) が正本です。

> **`make phase2` と、それを含む `make all` は、既存の VM と Terraform の状態を削除して作り直します。**
> 通常の更新や、再起動後の復旧には使いません（復旧は [日常運用](operations.md#ホストを再起動したあと)）。

## フェーズ

| コマンド | 実体 | 内容 |
|---|---|---|
| `make phase1` | [setup-host.sh](../automation/host-setup/setup-host.sh) | ホストに KVM / libvirt / Docker / Terraform / kubectl / Helm を入れ、グループとサービスを設定 |
| `make phase2` | [clean-and-deploy.sh](../automation/infrastructure/clean-and-deploy.sh) | 名前に `k8s` を含む VM・イメージ・Terraform 状態を削除し、VM、kubeadm クラスタ、Flannel を構築 |
| `make bootstrap` | [platform-deploy.sh](../automation/platform/platform-deploy.sh) | kubelet 証明書の承認、ArgoCD の初期導入、ESC の token、root Application の適用 |
| `make phase5` | [verify.sh](../automation/scripts/verify.sh) | kubeconfig を取得してクラスタの状態を確認 |

`make all` は上から順に実行し、失敗したところで止まります。出力は `automation/run.log` に追記されます。
`phase1` は sudo を使いますが、root では起動できません。一般ユーザーで実行します。

## リポジトリの外で用意するもの

| 用意するもの | 使われ方 |
|---|---|
| Pulumi ESC の環境 `ksera/k8s/secret` と、その access token | すべての Secret の元。token は bootstrap が Secret にする（[Secret 管理](secrets.md)） |
| Cloudflare の API token | wildcard 証明書の DNS-01 |
| Tailscale の OAuth クライアント | Operator と subnet router。tailnet 側で Split DNS を `192.168.122.101` に向ける |
| GitHub の OAuth App | ArgoCD のログイン。callback は `https://argocd.internal.qroksera.com/api/dex/callback` |
| GitHub の Deploy Key（書き込み可） | Image Updater がこのリポジトリへ digest を書き戻す |
| NFS の共有 | ホストの `/mnt/k8s-storage/nfs-share`。標準のフェーズは作らない（[setup-storage.sh](../automation/host-setup/setup-storage.sh) はディスクを操作するため、対象を確認して手で実行する） |

ESC に入れるキーは [Secret 管理](secrets.md#一覧) を参照してください。

## 設定ファイル

```bash
cp automation/settings.toml.example automation/settings.toml
```

`settings.toml`（Git 管理外）が効くのは、SSH の接続先、Pulumi の token、upgrade の対象版などスクリプトの動作だけです。
クラスタの構成はこのファイルからは生成されません。次の値はそれぞれのファイルに固定されています。

- VM の名前・IP・サイズ・Kubernetes の版: [variables.tf](../automation/infrastructure/variables.tf)。
  `clean-and-deploy.sh` は `terraform.tfvars` を毎回作り直し、ユーザー `k8suser` と `~/.ssh/id_rsa.pub`（なければ生成）を使います。
- Git の URL、ドメイン、NFS の場所、ESC の環境名: 各マニフェスト。

Kubernetes の版は [kubernetes-version.txt](../kubernetes-version.txt)（稼働中の版）から読みます。`variables.tf` の `kubernetes_version` を指定したときだけ、そちらが優先されます（[更新](updates.md#kubernetes)）。

## 手順

```bash
make phase1
# いったんログアウトして入り直す（libvirt / docker グループを反映）
make phase2
make bootstrap
make phase5
```

### bootstrap がすること

1. control-plane に SSH して `kubectl get nodes` を確認する。
2. [kubelet の serving 証明書](nodes.md#kubelet-の-serving-証明書) の CSR を承認し、全ノードの TLS を検証する。失敗したらここで止まる。
3. [upstream の定義](../manifests/platform/argocd-config/upstream/kustomization.yaml) と同じ版の ArgoCD を入れ、server の起動を待つ。
4. `external-secrets-system/pulumi-esc-token`（キー `accessToken`）を作る。token は環境変数 `PULUMI_ACCESS_TOKEN` か `settings.toml` から読む。
5. [bootstrap-root](../manifests/bootstrap/app-of-apps.yaml) を適用する。

ここから先は ArgoCD が [sync wave の順](gitops.md#application-一覧) に全 Application を入れます。
bootstrap の終了は、全体の収束を意味しません。Gateway の準備ができるまで ArgoCD の画面を見るには port-forward を使います。

```bash
ssh -L 127.0.0.1:8080:127.0.0.1:8080 ksera@192.168.10.11 \
  'kubectl -n argocd port-forward --address=127.0.0.1 svc/argocd-server 8080:80'
```

### phase5 の副作用

`make phase5` は control-plane の `/etc/kubernetes/admin.conf` を取得し、手元の kubeconfig をバックアップしてから上書きします
（`KUBECONFIG` を指定していればその最初のパス、なければ `~/.kube/config`）。
`CI=true` または `VERIFY_SKIP_SSH=true` では何もせず成功で終わります。

## 作り直すときの順序

作り直すと、VM のディスクにあるもの（PostgreSQL、Loki、Grafana、Alloy のデータ）は消えます。NFS 上のデータは残ります。

**作り直す前に**

1. PostgreSQL の手動バックアップを実行し、成功を確認する（[バックアップ](postgresql.md#バックアップ)）。dump は NFS に残ります。
2. API サーバーの暗号化鍵は作り直されるため、古い etcd snapshot を使う予定があるなら鍵を退避する（[暗号化](nodes.md#secret-の暗号化と監査ログ)）。

**作り直したあと**

1. ESO が Ready になり、ExternalSecret が Secret を作るのを待つ。
2. 空の PostgreSQL が起動したら、dump を復元する（[復元](postgresql.md#復元)）。
   Harbor は DB に復元済みの印があるまで起動しません。印は dump に含まれています。
3. NFS の PV は新しい名前で作られます。Harbor のイメージ、RustFS のオブジェクト、録画の spool は、
   古いディレクトリ（`<namespace>-<pvc>-pvc-<uid>`）から新しいディレクトリへ移します（[ストレージ](storage.md#nfs-のディレクトリ)）。
4. Harbor を作り直した場合は、project `sandbox`、Robot Account、保持ルールを設定し直す（[Harbor](storage.md#harbor)）。
   Harbor が空の間はアプリのイメージを pull できないため、各アプリの CI を再実行してイメージを公開します。
5. アプリの動作は [日常運用](operations.md#日々の確認) の項目で確認する。

Harbor のデータを引き継がない場合、アプリは「CI の再実行 → Image Updater の書き戻し → 同期」で戻ります。
