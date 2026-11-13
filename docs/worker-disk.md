# worker のディスク容量

worker の root ディスクは各60GiB。動画取得の最大5GiB、アプリが残す空き4GiB、
コンテナイメージ・ログを同じファイルシステムに置く。
`emptyDir.sizeLimit` や `ephemeral-storage.requests` は物理容量を増やさない。
クラスタ内の publish runner（ARC）は 2026-11-10 に撤去した。

## 構築時の容量

2026-10-30 に既存 VM を 30GiB から 60GiB へ拡張した記録は [archive](archive/worker-disk-20261030.md) を参照。
構築時の正本は [main.tf](../automation/infrastructure/main.tf) の `libvirt_volume.worker_disk.size`。
この宣言変更だけでは既存VMのpartition/filesystemは拡張されない。
`make all` / `make phase2` / `clean-and-deploy.sh` は再作成を含むため、拡張には使わない。
既存環境のTerraform stateに古い容量が残る場合は、次回操作前にrefreshとplanの差分を確認し、
容量変更を理由にVMやvolumeを置換するplanを適用しない。

## 同じ構成を拡張するときの手順

以下は既存の `/dev/vda` → `/dev/vda1` → ext4 に限定した手順。
実VM名、volumeの保存先、ホストの空き、現在容量、partitionの開始位置を確認し、1台ずつ実施する。
稼働中のqcow2に `qemu-img resize` / `virsh vol-resize` を使わず、
[virsh blockresize](https://www.libvirt.org/manpages/virsh.html#blockresize) を使う。

ホスト上の例（60GiBへの拡張が未実施の場合のみ）:

```bash
vm=k8s-worker2-3e249c58
node_ip=192.168.122.12
virsh -c qemu:///system domblklist "$vm" --details
virsh -c qemu:///system domblkinfo "$vm" vda
df -h /var/lib/libvirt/images
ssh -n "k8suser@$node_ip" 'findmnt -n -o SOURCE,FSTYPE /; sudo -n sfdisk --dump /dev/vda'
# 現在容量が目標より小さく、ホストに余裕があることを確認してから実行。
virsh -c qemu:///system blockresize "$vm" vda 64424509440B
ssh -n "k8suser@$node_ip" 'sudo -n growpart -N /dev/vda 1'
# dry-runで開始位置が不変、partition 1の末尾だけが拡張されることを確認。
ssh -n "k8suser@$node_ip" 'sudo -n growpart /dev/vda 1 && sudo -n resize2fs /dev/vda1 && df -h /'
```

`growpart` が失敗したら、そのまま後続を実行しない。すでに拡張済みなら不要な再実行を避ける。
容量を元に戻すための縮小は行わない。

filesystem拡張後もNodeのcapacity/allocatableが旧サイズのままなら、
kubeletのみを1台ずつ再起動して再認識させる。containerdやVMは再起動しない。

```bash
ssh -n "k8suser@$node_ip" 'sudo -n systemctl restart kubelet; systemctl is-active kubelet containerd'
kubectl get node k8s-worker2 -o jsonpath='{.status.capacity.ephemeral-storage}{" / "}{.status.allocatable.ephemeral-storage}{"\n"}'
kubectl get nodes
kubectl get pods -A
```

Node Ready、更新されたcapacity/allocatable、既存PodのUID・restartCount、
media workerの一時領域の空き（5GiB + 4GiB以上）を確認して次のノードへ進む。

## 容量監視

既存VictoriaMetricsが各kubeletの `/metrics/cadvisor` からrootの容量・使用量だけを60秒ごとに収集する。
追加Pod、追加RBAC、アプリの変更は不要。

- 警告: 空き推定値12GiBまたは20%未満が10分継続。
- 重大: 空き推定値9GiBまたは15%未満が5分継続。
- 計測不能: Node台帳に対して容量・使用量の欠損または容量0が5分継続。

割合の条件はkubeletのimagefs空き15%・nodefs空き10%の退避基準も考慮する。
空きはcapacity−usageの推定値で、非rootの使用可能容量 `f_bavail` と同一とは限らない。
アプリの `SPOOL_MIN_FREE_BYTES=4GiB` は維持し、枯渇時にも録画や他Podの領域を守る。
Grafana「03 ストレージ」の最下部に全ノードの空き・使用率を表示する。
PVC/NFSの容量とは別の指標であり、namespace/PVC選択によって絞り込まれない。
