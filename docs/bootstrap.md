# 初期構築

実行順序は [run.sh](../automation/scripts/run.sh)、各コマンドの入口は [Makefile](../Makefile) が正本です。
**`make phase2` とそれを含む `make all` は既存環境を削除して構築し直します。通常の更新・再起動後の復旧には使いません。**

## 実際のフェーズ

| コマンド | 実体 | 主な処理 |
|---|---|---|
| `make phase1` / `make vm` | [setup-host.sh](../automation/host-setup/setup-host.sh) | ホストの apt 更新、KVM/libvirt/Docker/CLI 導入、グループ・サービス設定 |
| `make phase2` / `make k8s` | [clean-and-deploy.sh](../automation/infrastructure/clean-and-deploy.sh) | VM・Terraform 状態の削除、tfvars 生成、Terraform による VM / kubeadm / Flannel 構築 |
| `make bootstrap` | [platform-deploy.sh](../automation/platform/platform-deploy.sh) | serving 証明書自動承認・全台TLS検証、ArgoCD 初期導入、ESC token、root Application |
| `make phase5` / `make verify` | [verify.sh](../automation/scripts/verify.sh) | kubeconfig 取得とクラスタ状態確認 |

`make all` はこの順序で実行し、途中の失敗で停止します。`phase1` の別名は `vm` ですが、この段階では VM は作成しません。
`run.sh` 経由の出力は `automation/run.log` に追記します。

## 事前準備

1. Ubuntu 24.04 LTS、KVM、sudo を使える一般ユーザー、ネットワーク・ディスク容量を用意します。
   `setup-host.sh` は root での起動を拒否するため、`sudo make all` ではなく一般ユーザーから実行します。
2. ローカル設定を作成します。既存の設定を上書きしないでください。

   ```bash
   cp automation/settings.toml.example automation/settings.toml
   ```

3. SSH ユーザー・鍵・IP、Pulumi token を設定します。`settings.toml` は Git 管理外です。
   Secret の必要なキーと外部設定は [Secret 管理](secrets.md) と [アクセス経路](access.md) を確認します。
4. NFS 共有を用意します。標準の phase1 は [setup-storage.sh](../automation/host-setup/setup-storage.sh) を実行しません。
   この補助スクリプトはディスク操作を含むため、対象デバイスと既存データを確認して必要な場合だけ実行します。

## 設定の適用範囲

`settings.toml` は全構成を生成する設定ではありません。
現在の `clean-and-deploy.sh` は `terraform.tfvars` を毎回生成し、ユーザー `k8suser`、IP `.10/.11/.12`、
`~/.ssh/id_rsa.pub` を使用します。鍵がなければ `~/.ssh/id_rsa` を作成します。
`settings.toml` の IP や鍵だけを変えても、このフェーズの値は変わりません。

初期 Kubernetes 版は [variables.tf](../automation/infrastructure/variables.tf) の `kubernetes_version`、
既存ノードの更新先は `settings.toml` の `[upgrade].target_version` です。
ArgoCD の Git URL、DNS、NFS、ESC environment も各マニフェストで固定されています。
環境を変える場合は [contracts](../manifests/contracts/home-lab/) と対応する実体を同時に更新します。

## 構築手順

初回は段階実行すると、phase1 によるグループ変更後に再ログインできます。

```bash
make phase1
# 再ログイン後、libvirt / docker へのアクセスと NFS を確認
sudo -v
make phase2
make bootstrap
make phase5
```

`phase2` は名前に `k8s` を含む VM、関連イメージ、Terraform 状態などを削除します。
PostgreSQL の DB は worker2 の VM ディスク（local-path）にあり、VM と一緒に消えます。
既存クラスタを再構築する場合は、先に repo1 への手動 full backup の成功を確認し、PV/PVC の対応、オブジェクトのバックアップ、外部認証情報を保全し、
[PostgreSQL の復旧](shared-postgresql.md#再起動と再構築) を確認してください。
NFS にファイルが残ることと、新クラスタが正しいデータを再利用できることは別です。

Terraform / Ansible / kubectl / Helm は phase1 の通常インストール経路で導入します。
検証用 CLI は [mise toolchain のセットアップ](reference.md#ローカルtoolchainのセットアップ)で追加できます。

## Bootstrap が行うこと

1. control-plane に SSH 接続し `kubectl get nodes` を確認。
2. [kubelet serving 証明書](kubelet-metrics.md)の自動承認処理を導入し、全ノードのTLS検証を待つ。
   失敗時は以降の初期導入を停止する。初回・更新とも手動のCSR承認は不要。
3. [ArgoCD upstream](../manifests/platform/argocd-config/upstream/kustomization.yaml) から固定 tag を読み、
   本体と ApplicationSet CRD を初期適用。server の起動を待ち、insecure 設定と bootstrap 用 Ingress を適用。
4. `external-secrets-system/pulumi-esc-token`（キー `accessToken`）と provider 用 RBAC を作成。
5. [bootstrap-root](../manifests/bootstrap/app-of-apps.yaml) を適用。

トークンは `PULUMI_ACCESS_TOKEN` または設定ローダーから取得します。
bootstrap の SSH/SCP は既定の SSH 設定を使い、verify と異なり `K8S_SSH_KEY` を `-i` に渡しません。
必要なら SSH config / agent を用意します。

証明書準備にはローカルの SSH/SCP、control-plane の Python 3 / kubectl（内蔵Kustomize）が必要。
phase1 のホスト準備と標準Ubuntuノードで揃う。ノード台帳は [nodes.json](../manifests/infrastructure/security/kubelet-serving-certificates/nodes.json) を参照。

bootstrap 終了は全 child Application の収束完了ではありません。
CRD、ESO、証明書、DB の復元、アプリ起動を別途確認します。
特に Harbor は復元済みマーカーを要求するため、空の DB を作成しただけでは起動しません。
初回の空環境でも現在のデータ初期化・復元方針を決める必要があります。
初回以降の ArgoCD 更新は `argocd-core` が管理します。

## 検証時の副作用と限界

`make phase5` は既定で `k8suser@192.168.122.10` から `/etc/kubernetes/admin.conf` を取得し、
既存 kubeconfig をバックアップして書き換えます。`KUBECONFIG` 指定時は最初のパス、未指定時は `~/.kube/config` が対象です。
また root / platform Application の hard refresh を試みます。

```bash
# kubeconfig の保存先を明示する例
KUBECONFIG="$HOME/.kube/k8s-myhome" make phase5
```

Node / Pod / Application / ESO の状態を確認しますが、Namespace や Gateway / LoadBalancer の一部欠落は警告のみです。
`CI=true`、`CI=1`、`VERIFY_SKIP_SSH=true` では SSH と kubeconfig 更新を含むライブ検証全体をスキップし、成功終了します。
静的検証と使い捨て VM の実構築テストは [検証・参照](reference.md) にまとめています。

## API Server のセキュリティ設定

新規構築では Secret 保存時暗号化と Metadata 監査を有効にします。既存クラスタの移行、鍵の保管、検証は
[API Server セキュリティ](apiserver-security.md) を参照してください。
