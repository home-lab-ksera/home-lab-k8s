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

合計 6 vCPU / 13GiB。worker1 の VM 定義とディスクは 2026-11-13 に削除した（共用のベースイメージ `ubuntu-base-*.img` は worker2 が使うため残す）。
control-plane の kubelet drop-in は `/etc/kubernetes/kubelet.conf.d/50-control-plane-reserved.conf` に置き、
`/etc/default/kubelet` の `--config-dir` で読み込む。`/var/lib/kubelet/config.yaml` は kubeadm upgrade で再生成されるため使わない。
経緯は [全体最適化計画](../tasks/capacity-optimization-plan.md) の 15 節。



## 再測定

ホストで次を確認する。

```bash
free -m; swapon --show
cat /proc/pressure/cpu /proc/pressure/memory
ps -eo rss,args --sort=-rss | grep '[q]emu' | awk '{printf "%d MiB\n", $1/1024}'
kubectl -n kube-system get pods | grep -E 'controller-manager|scheduler'
```

swap 使用量が再び増える場合は、worker2 を 7GiB へ縮小するか、ホスト swap を zram に置き換える。
縮小前に、全ノードの memory requests 合計が縮小後の allocatable 合計に収まることを確認する。
