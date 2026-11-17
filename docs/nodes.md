# ホストとノード

## ホスト

| 項目 | 値 |
|---|---|
| 機種 | Intel N100（4 コア、SMT なし）、RAM 15GiB、swap 4GiB（ファイル） |
| OS | Ubuntu 26.04 LTS |
| ディスク | NVMe 476GB（OS と VM のイメージ）、HDD 3.6TB（USB 接続の外付けケース Logitec LHR で RAID1、`/dev/sda1` → `/mnt/k8s-storage`） |
| LAN | Wi-Fi のみ（`192.168.10.11`）。有線は使っていない |
| 接続 | `ssh ksera@192.168.10.11`。`virsh` と `kubectl` をそのまま使える |

ホストの次の設定は手作業で入れたもので、このリポジトリにはありません。

- NFS: `/mnt/k8s-storage/nfs-share` を `192.168.122.0/24` にだけ export（`/etc/exports`）。
- ufw: VM のネットワークから node_exporter（9100）への接続を許可。
- `prometheus-node-exporter`: `192.168.122.1:9100`。textfile collector が apt の保留更新と再起動待ちを出力します。
  Debian の既定では `/mnt` 以下を収集しないため、`/etc/default/prometheus-node-exporter` の `ARGS` で
  `--collector.filesystem.mount-points-exclude` から `mnt` を外しています（HDD のアラートに必要）。
- unattended-upgrades: セキュリティ更新を毎日適用。
- Tailscale: ホスト自身も tailnet に参加。`--accept-routes=false` を維持します
  （クラスタの subnet router が広告する `192.168.122.0/24` は、ホスト自身の VM ネットワークのため）。

ホストの SSH は Wi-Fi 経由です。`wpa_supplicant` と NetworkManager を止めると接続が切れます。
### HDD

- RAID はケースの中で組まれていて、ホストからは 1 台のディスク（"H/W RAID1"）にしか見えません。
  **中の 2 台のどちらかが壊れても、ホストからは分かりません。** 気づく手段はケース前面の LED とブザーだけです。
- ホストから分かるのは「マウントが消えた」「読み取り専用になった」「空きが減った」で、
  それぞれ `HostStorageUnavailable`、`HostStorageReadOnly`、`HostStorageSpaceLow` が通知します。
- ケースは USB 3 のポートにつなぎます（5Gbps）。USB 2 のポートだと 480Mbps になり、NFS 上のすべてが遅くなります。
  確かめるには、ホストで `cat /sys/bus/usb/devices/*/speed` を見て、ケース（`idVendor` が `0789`）が `5000` であることを確認します。
- **VM が動いている間はケースを抜きません。** 抜くとファイルシステムが切り離され、NFS が止まります
  （戻し方は [障害対応](troubleshooting.md#hdd-が外れた)）。抜くときは、先に VM を止め、`/mnt/k8s-storage` をアンマウントします。

## VM

宣言は [variables.tf](../automation/infrastructure/variables.tf) と [main.tf](../automation/infrastructure/main.tf) です。

| VM | vCPU / メモリ | ディスク | 役割 |
|---|---|---|---|
| `k8s-control-plane` | 2 / 5GiB | 50GiB | control-plane。アプリの Pod も動く |
| `k8s-worker2` | 4 / 8GiB | 60GiB | local-path の PV（PostgreSQL、Loki、Grafana、Alloy）を持つ |

- CPU mode は `host-model`、memballoon の free page reporting を有効にしています。
- ノード名が `k8s-worker2` なのは、local-path の PV がこの名前に固定されているためです。名前を変えると PV を使えなくなります。
- VM のユーザーは `k8suser` で、VM 内では sudo にパスワードが要りません。ホストから `ssh k8suser@192.168.122.10`（または `.12`）で入ります。
- 各 VM はホストの NFS を `/mnt/nfs-storage` にマウントします。local-path の実体は VM のディスク上の `/mnt/k8s-storage` です。

### 容量の考え方

VM の合計は 6 vCPU / 13GiB で、ホストは 4 コア / 15GiB です。
VM の合計がホストの実メモリに近いため、qemu のメモリがホストの swap に出ることがあり、
出入りが続くと control-plane の応答が遅くなります。`Host*` のアラートがこれを通知します（[監視](observability.md#アラート)）。

測り直すときはホストで次を見ます。

```bash
free -m; swapon --show
cat /proc/pressure/cpu /proc/pressure/memory
ps -eo rss,args --sort=-rss | grep '[q]emu' | awk '{printf "%d MiB\n", $1/1024}'
kubectl describe node | grep -A8 'Allocated resources'
```

VM を縮めるときは、先に全ノードの memory requests の合計が縮小後の allocatable に収まることを確認します。
測定値の記録は [tasks/improvement-progress.md](../tasks/improvement-progress.md) の「再測定」にあります。

## ノードに置いている設定

すべて構築時に Terraform と cloud-init が配置します。稼働中のノードで変えたら、同じ内容を main.tf にも反映します。

| 対象 | ファイル | 内容 |
|---|---|---|
| control-plane の kubelet | `/etc/kubernetes/kubelet.conf.d/50-control-plane-reserved.conf` | systemReserved 200m / 512Mi、evictionHard memory.available 300Mi |
| worker の kubelet | `/etc/kubernetes/kubelet.conf.d/60-image-gc.conf` | image GC を 70% で開始、60% まで削除 |
| 全 VM の journald | `/etc/systemd/journald.conf.d/50-size.conf` | 500MB まで |
| control-plane の API サーバー | `/etc/kubernetes/security/` | Secret の暗号化鍵と監査ポリシー |

kubelet の drop-in は `/etc/default/kubelet` の `--config-dir` で読み込みます。
`/var/lib/kubelet/config.yaml` は kubeadm upgrade で作り直されるため、設定はここに書きません。

## kubelet の serving 証明書

kubelet は `serverTLSBootstrap: true` で、クラスタ CA が署名した証明書を 10250 番で使います。
VictoriaMetrics と metrics-server はこの証明書を検証して kubelet に接続します（`--kubelet-insecure-tls` は使いません）。

この証明書の CSR は自動では承認されません。初回は `make bootstrap` が承認し、更新時（有効期間は 1 年）は手で承認します。
`KubeletServingCSRPending` が届いたら、要求元を確認して承認します。

```bash
kubectl get csr | grep Pending
kubectl get csr <名前> -o jsonpath='{.spec.username}{"\n"}{.spec.signerName}{"\n"}'   # system:node:<ノード名> / kubernetes.io/kubelet-serving
kubectl certificate approve <名前>
```

まとめて承認して検証するコマンドもあります。対象は [台帳](../automation/scripts/kubelet-serving-nodes.json) にあるノードが自分で出した CSR だけです。

```bash
make kubelet-certificates                                               # 未承認分を承認して検証
bash automation/scripts/kubelet-serving-certificates.sh --verify-only   # 検証だけ
```

承認しないまま期限が切れると、メトリクスの収集と `kubectl top` が TLS 検証で失敗します（`KubeletVolumeScrapeUnavailable`）。
Pod の実行には影響しません。ノード名や IP を変えるときは、台帳と Terraform の両方を直します。

## control-plane の証明書

kubeadm が発行する証明書は 1 年有効で、`kubeadm upgrade` のたびに更新されます。
残り 45 日を切ると `ControlPlaneCertificateExpiring` が通知します。upgrade の予定がなければ、control-plane で更新します。

```bash
sudo kubeadm certs check-expiration
sudo kubeadm certs renew all   # 実行後、static Pod（apiserver など）を再起動する
```

## Secret の暗号化と監査ログ

構築時に [configure.py](../automation/infrastructure/apiserver-security/configure.py) が control-plane で次を設定します。

- **保存時暗号化**: Secret を AES-CBC で暗号化して etcd に保存します。鍵は `/etc/kubernetes/security/encryption-config.yaml`
  （root のみ読める）にあり、Git・Terraform state・ログには出しません。
- **監査ログ**: `/var/log/kubernetes/audit/audit.log` に Metadata レベルで記録します（本文や Secret の値は記録しません）。
  100MB × 10 世代、最大 30 日です。

監査で記録するのは、すべての変更操作、人の操作、Secret へのアクセスです。
health 確認、metrics、lease の更新、ServiceAccount や kubelet などによる Secret 以外の読み取りは記録しません。

注意点は次のとおりです。

- 鍵を失うと etcd の snapshot から Secret を復元できません。snapshot とは別の場所に鍵を保管します。
- 既存の鍵を消したり作り直したりしません。鍵を替えるときは、新しい鍵を追加して先頭に置き、API サーバーを再起動し、
  全 Secret を保存し直してから古い鍵を外します（[Kubernetes の手順](https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/)）。
- 監査ポリシーは API サーバーの起動時にしか読まれません。変えるときは control-plane で
  `sudo python3 configure.py prepare` を実行してファイルを書き直し、API サーバーを再起動します。
  変更前のファイルを退避し、`/livez` が戻らなければ元に戻します。
- 監査ログはノードのローカルにしかありません。

## ディスクを広げる

main.tf のサイズを変えても、既存の VM のディスクは広がりません。
`make phase2` は VM を作り直すため、拡張には使いません。稼働中の VM は次の手順で 1 台ずつ広げます（`/dev/vda1` が ext4 の構成が前提）。

```bash
vm=k8s-worker2-<suffix>          # virsh list で確認
node_ip=192.168.122.12
virsh domblkinfo "$vm" vda
df -h /var/lib/libvirt/images
virsh blockresize "$vm" vda <新しいサイズ>B
ssh "k8suser@$node_ip" 'sudo growpart -N /dev/vda 1'    # dry-run。開始位置が変わらないことを確認
ssh "k8suser@$node_ip" 'sudo growpart /dev/vda 1 && sudo resize2fs /dev/vda1 && df -h /'
```

- 稼働中の qcow2 に `qemu-img resize` や `virsh vol-resize` は使いません。`virsh blockresize` を使います。
- `growpart` が失敗したら、後続のコマンドを実行しません。縮小はしません。
- Node の capacity が古いままなら、そのノードの kubelet だけを再起動します（`sudo systemctl restart kubelet`）。

worker のディスクには、コンテナイメージ、ログ、local-path の PV、media worker の一時領域（最大 5GiB）が同居します。
空きが減ると `KubernetesNodeDiskSpaceLow`（12GiB または 20% 未満）と `KubernetesNodeDiskSpaceCritical`（9GiB または 15% 未満）が通知します。
