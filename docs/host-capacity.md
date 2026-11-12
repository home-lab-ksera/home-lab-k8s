# ホスト容量と VM サイズ

## 前提

ホストは Intel N100（4コア・SMTなし）/ RAM 15GiB / swap 4GiB（ファイル）。
VM の vCPU とメモリは物理資源を共有するため、VM 合計がホストの実メモリを超えると
qemu プロセスがホスト swap へ追い出され、control-plane の応答遅延として現れる。

## 現行構成（2026-11-12 から）

| VM | vCPU / メモリ | 備考 |
|---|---|---|
| k8s-control-plane | 2 / 5120 MiB | taint なし。kubelet drop-in で systemReserved 200m / 512Mi、evictionHard memory.available 300Mi |
| k8s-worker2 | 4 / 8192 MiB | local-path の PV（PostgreSQL、Loki、Grafana、Alloy）を持つ |

合計 6 vCPU / 13GiB。worker1 は停止して VM 定義とディスクを残している（切り戻し用）。
control-plane の kubelet drop-in は `/etc/kubernetes/kubelet.conf.d/50-control-plane-reserved.conf` に置き、
`/etc/default/kubelet` の `--config-dir` で読み込む。`/var/lib/kubelet/config.yaml` は kubeadm upgrade で再生成されるため使わない。
経緯は [全体最適化計画](../tasks/capacity-optimization-plan.md) の 15 節。

以下は 3VM 構成時の記録。

## 2026-11-10 の変更

### 変更前の実測（10:01〜10:24 JST）

| 項目 | 値 |
|---|---|
| VM 割当 | 6GiB × 3 = 18GiB、vCPU 計12 |
| ホスト swap | 4094 / 4095 MiB 使用、swap-out 最大約10MB/s |
| ホスト CPU PSI some | avg300 25〜29% |
| qemu RSS | 5.1 / 5.0 / 4.1 GiB |
| ゲスト working set | control-plane 3.0 / worker1 3.2 / worker2 3.8 GiB |
| control-plane の CPU 使用 | 約550m |

kube-controller-manager（27回）と kube-scheduler（26回）の再起動は
`leaderelection lost`（apiserver への lease 更新タイムアウト）で、ホスト過負荷の症状と判断した。

### 変更内容

- 全 VM の `memballoon` に `freePageReporting='on'`。ゲストで解放されたページをホストへ返す。
  ゲストの page cache は解放されないため、時間とともに RSS は再び増える。
- control-plane を 6GiB / 4 vCPU から 4GiB / 2 vCPU へ縮小。worker は 6GiB / 4 vCPU のまま。

反映は Terraform ではなく virsh で行った。dmacvicar/libvirt 0.7 では memory / vcpu / xml の変更が
VM の再作成になるため、**既存クラスタに `terraform apply` を実行しない**。
Terraform の既定値と [libvirt-domain.xsl](../automation/infrastructure/libvirt-domain.xsl) は
次回 `make phase2` で同じ構成を作るための定義である。

### 手順の記録

1. `virsh dumpxml --inactive` で全 VM の定義、etcd snapshot をホストの `~/vm-tuning-20261110/` に退避。
2. 定義を編集して `virsh define`。定義の変更は次回の起動で反映される（ゲスト内の再起動では反映されない）。
3. control-plane: `virsh shutdown` → `virsh start`。drain は不要（static Pod と DaemonSet のみ）。API 停止は約1分。
4. worker（worker2 → worker1）: cordon → kubelet 停止 → `crictl stop --timeout 30` で全コンテナを停止 →
   `virsh shutdown` → `virsh start` → Ready 確認 → uncordon。
   kubelet の `shutdownGracePeriod` は 0s のため、OS の停止に任せずコンテナを先に停止する。
   drain しないのは、worker 1台の memory requests が他方の空きに収まらず Pending になるため。

切り戻しは退避した XML を `virsh define` し、同じ手順で再起動する。

### 変更後（10:42 JST、再起動直後）

| 項目 | 値 |
|---|---|
| ホスト swap | 97 MiB |
| ホスト CPU PSI some | avg10 17% |
| qemu RSS | 4.2 / 4.0 / 3.5 GiB |
| ArgoCD | 51 Application すべて Synced / Healthy |

再起動直後の値であり、page cache が増えた後の定常値は数日後に再測定する。

## 再測定

ホストで次を確認する。

```bash
free -m; swapon --show
cat /proc/pressure/cpu /proc/pressure/memory
ps -eo rss,args --sort=-rss | grep '[q]emu' | awk '{printf "%d MiB\n", $1/1024}'
kubectl -n kube-system get pods | grep -E 'controller-manager|scheduler'
```

swap 使用量が再び増える場合は、worker を 5GiB へ縮小するか、ホスト swap を zram に置き換える。
worker 縮小前に、両 worker の memory requests 合計が縮小後の allocatable 合計に収まることを確認する。
