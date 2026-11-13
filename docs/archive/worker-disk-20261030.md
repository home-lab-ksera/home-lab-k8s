# worker ディスクの拡張（2026-10-30）

[worker のディスク容量](../worker-disk.md) から移した当時の記録。

物理ホスト `ksera-T100`（SSH: `ksera@192.168.122.1`）のSSD空き約297GiBを確認し、
以下を30GiBから60GiBへオンライン拡張した。VM再作成・再起動・Pod削除は行わない。

| VM | Node / SSH | root | 拡張後のfilesystem容量 / 空き（概数） |
|---|---|---|---|
| k8s-worker1-3e249c58 | k8s-worker1 / k8suser@192.168.122.11 | /dev/vda1、ext4 | 57.1GiB / 36.0GiB（2026-11-12 に撤去） |
| k8s-worker2-3e249c58 | k8s-worker2 / k8suser@192.168.122.12 | /dev/vda1、ext4 | 57.1GiB / 36.1GiB |

仮想ディスク60GiBとfilesystem容量は、boot partitionやext4の管理領域の分だけ異なる。
kubeletを1台ずつ再起動し、両workerのNode capacity約57.1GiB / allocatable約51.4GiBを確認した。
既存の常駐PodにUID変更・restartCount増加はなく、全Node Readyを確認した。
変更前のpartition tableはホストの `~/.local/state/k8s-worker-disk-20261030/` に保存。
データバックアップではなく、変更前の区画配置を確認するための記録である。
