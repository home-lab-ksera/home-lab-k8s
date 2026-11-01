# Phase 1: Host Setup

Ubuntu 24.04 LTS ホストで home-lab-k8s の VM / Kubernetes 構築前提を準備します。通常の実行入口は repo root の `make phase1` です。

## 実行入口

```bash
make phase1
```

実体は `automation/host-setup/setup-host.sh` です。`make all` の先頭でも同じ処理を実行します。

## 実行内容

- QEMU/KVM + libvirt のインストール
- Docker と基本ツールのインストール
- Terraform / Ansible / kubectl / Helm のインストール
- `libvirt` / `kvm` / `docker` グループへのユーザー追加
- `libvirtd` / `docker` サービスの有効化
- 仮想化サポートの確認

Terraform / Ansible / kubectl / Helm はホストへ直接導入します。検証用の mise toolchain は [検証手順](../../docs/reference.md) を参照してください。

## ストレージ補助スクリプト

外部USBストレージやNFS共有をローカルで準備する場合のみ手動で使います。

```bash
automation/host-setup/setup-storage.sh
automation/host-setup/verify-setup.sh
```

`setup-storage.sh` は対話式でストレージデバイスを選択し、パーティションや `/etc/fstab` を変更する可能性があります。実行前に対象デバイスとバックアップを確認してください。

## 次のステップ

`make phase1` 後はログアウト/ログインしてグループメンバーシップを反映し、次へ進みます。

```bash
make phase2
make bootstrap
make phase5
```

全体を通す場合は `make all` を使います。

## トラブルシューティング

- 仮想化が無効な場合は BIOS/UEFI で Intel VT-x / AMD-V を有効化します。
- グループメンバーシップが反映されない場合はログアウト/ログイン、または `newgrp libvirt && newgrp docker` を実行します。
- ストレージデバイスが認識されない場合は `lsblk` や `fdisk -l` で対象を確認します。
- NFS設定が失敗する場合は `/etc/exports` と `nfs-kernel-server` の状態を確認します。

```bash
systemctl status libvirtd
systemctl status docker
systemctl status nfs-kernel-server
journalctl -u libvirtd
journalctl -u docker
journalctl -u nfs-kernel-server
```
