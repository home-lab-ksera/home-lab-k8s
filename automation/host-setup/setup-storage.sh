#!/bin/bash

# Phase 1: 外部ストレージ設定スクリプト
# USB外部ストレージをk8s用に設定

set -euo pipefail

# 設定ファイル読み込み
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOMATION_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -f "$AUTOMATION_DIR/scripts/settings-loader.sh" ]]; then
    source "$AUTOMATION_DIR/scripts/settings-loader.sh" load 2>/dev/null || true
fi

# 共通色設定スクリプトを読み込み
source "$SCRIPT_DIR/../scripts/common-logging.sh"

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   log_error "This script should not be run as root"
   exit 1
fi

log_status "Starting external storage setup for k8s cluster"

detect_usb_storage_device() {
    local root_source=""
    local root_disk_name=""
    local root_parent=""
    local name=""
    local size=""
    local type=""
    local tran=""
    local rota=""
    local best_usb_name=""
    local best_usb_size=0
    local best_ssd_name=""
    local best_ssd_size=0

    root_source="$(findmnt -n -o SOURCE / 2>/dev/null || true)"
    if [[ -n "$root_source" && -b "$root_source" ]]; then
        root_parent="$(lsblk -no PKNAME "$root_source" 2>/dev/null || true)"
        if [[ -n "$root_parent" ]]; then
            root_disk_name="$root_parent"
        else
            root_disk_name="$(basename "$root_source")"
        fi
    fi

    while read -r name size type tran rota; do
        [[ "$type" == "disk" ]] || continue
        [[ "$tran" == "usb" ]] || continue
        [[ -n "$root_disk_name" && "$name" == "$root_disk_name" ]] && continue

        if (( size > best_usb_size )); then
            best_usb_size=$size
            best_usb_name="$name"
        fi

        if [[ "$rota" == "0" ]] && (( size > best_ssd_size )); then
            best_ssd_size=$size
            best_ssd_name="$name"
        fi
    done < <(lsblk -dbn -o NAME,SIZE,TYPE,TRAN,ROTA)

    if [[ -n "$best_ssd_name" ]]; then
        echo "$best_ssd_name"
        return 0
    fi

    if [[ -n "$best_usb_name" ]]; then
        log_warning "非回転ディスク判定のUSBデバイスが見つからないため、最大容量のUSBデバイスを使用します: /dev/$best_usb_name" >&2
        echo "$best_usb_name"
        return 0
    fi

    return 1
}

# 1. Detect USB storage devices
log_status "Detecting USB storage devices..."
echo "Available block devices:"
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,LABEL,UUID | grep -E "(disk|part)"

echo ""
log_status "USB devices:"
lsusb | grep -i "storage\|disk\|drive" || echo "No USB storage devices found in lsusb output"

echo ""
log_status "Disk usage information:"
df -h | head -1
df -h | grep -E "(sd[b-z]|nvme|mmc)" || echo "No additional storage devices mounted"

# 2. Device selection (settings override or automatic detection)
echo ""
if [[ -n "${HOST_SETUP_USB_DEVICE_NAME:-}" ]]; then
    DEVICE_NAME="${HOST_SETUP_USB_DEVICE_NAME}"
    log_status "Using device from settings: $DEVICE_NAME"
else
    if DEVICE_NAME="$(detect_usb_storage_device)"; then
        log_status "Auto-detected USB storage device: /dev/$DEVICE_NAME"
    else
        log_error "USB接続ストレージを自動検出できませんでした"
        log_error "settings.toml の [host_setup] usb_device_name で明示指定するか、USBストレージ接続状態を確認してください"
        exit 1
    fi
fi

# Validate device
if [[ ! -b "/dev/$DEVICE_NAME" ]]; then
    log_error "Device /dev/$DEVICE_NAME does not exist"
    exit 1
fi

# Safety check
echo ""
log_warning "WARNING: This will set up /dev/$DEVICE_NAME for k8s storage"
log_warning "Current partition table for /dev/$DEVICE_NAME:"
sudo -n fdisk -l /dev/$DEVICE_NAME 2>/dev/null || echo "Could not read partition table"

echo ""
if [[ "${AUTOMATION_AUTO_CONFIRM_OVERWRITE:-}" == "true" ]]; then
    log_status "Auto-confirming device selection: /dev/$DEVICE_NAME"
    CONFIRM="yes"
else
    log_input "Are you sure you want to proceed with /dev/$DEVICE_NAME? (yes/no): "
    read -r CONFIRM
fi

if [[ "$CONFIRM" != "yes" ]]; then
    log_status "Operation cancelled"
    exit 0
fi

# 3. Get UUID of the device/partition
DEVICE_PATH="/dev/$DEVICE_NAME"
if [[ "$DEVICE_NAME" =~ [0-9]$ ]]; then
    # Already a partition
    PARTITION_PATH="$DEVICE_PATH"
else
    # Assume first partition
    if [[ "$DEVICE_NAME" =~ ^nvme ]]; then
        PARTITION_PATH="${DEVICE_PATH}p1"
    else
        PARTITION_PATH="${DEVICE_PATH}1"
    fi
fi

log_status "Using partition: $PARTITION_PATH"

# Check if partition exists
if [[ ! -b "$PARTITION_PATH" ]]; then
    log_warning "Partition $PARTITION_PATH does not exist"
    log_input "Do you want to create a new partition? (yes/no): "
    read -r CREATE_PARTITION
    
    if [[ "$CREATE_PARTITION" == "yes" ]]; then
        log_status "Creating new partition on $DEVICE_PATH"
        sudo -n parted "$DEVICE_PATH" --script -- mklabel gpt
        sudo -n parted "$DEVICE_PATH" --script -- mkpart primary ext4 0% 100%
        
        # Format the partition
        log_status "Formatting partition with ext4..."
        sudo -n mkfs.ext4 -F "$PARTITION_PATH"
        
        log_status "Setting filesystem label..."
        sudo -n e2label "$PARTITION_PATH" "k8s-storage"
    else
        log_error "Cannot proceed without a valid partition"
        exit 1
    fi
fi

# Get UUID
UUID=$(sudo -n blkid -s UUID -o value "$PARTITION_PATH")
if [[ -z "$UUID" ]]; then
    log_error "Could not get UUID for $PARTITION_PATH"
    exit 1
fi

log_status "Found UUID: $UUID"

# 4. Create mount directories
MOUNT_BASE="/mnt/k8s-storage"
log_status "Creating mount directories..."
sudo -n mkdir -p "$MOUNT_BASE"

# 5. Set up fstab entry
log_status "Setting up persistent mount in /etc/fstab..."

# Backup fstab
sudo -n cp /etc/fstab /etc/fstab.backup.$(date +%Y%m%d_%H%M%S)

# Remove any existing entry for this UUID
sudo -n sed -i "/UUID=$UUID/d" /etc/fstab

# Add new entry
echo "UUID=$UUID $MOUNT_BASE ext4 defaults,noatime 0 2" | sudo -n tee -a /etc/fstab

# 6. Mount the storage
log_status "Mounting storage..."
sudo -n systemctl daemon-reload

# Check if already mounted
if mountpoint -q "$MOUNT_BASE"; then
    log_warning "Storage already mounted at $MOUNT_BASE"
else
    # Attempt to mount
    if sudo -n mount "$MOUNT_BASE" 2>/dev/null; then
        log_status "Storage successfully mounted at $MOUNT_BASE"
    else
        log_error "Failed to mount storage at $MOUNT_BASE"
        exit 1
    fi
fi

# Final verification
if mountpoint -q "$MOUNT_BASE"; then
    log_status "Storage is available at $MOUNT_BASE"
else
    log_error "Storage is not available at $MOUNT_BASE"
    exit 1
fi

# 7. Create subdirectories after mounting
log_status "Creating subdirectories..."
sudo -n mkdir -p "$MOUNT_BASE/nfs-share"

# 8. Set permissions
log_status "Setting permissions..."
sudo -n chown -R "$USER:$USER" "$MOUNT_BASE"
sudo -n chmod -R 755 "$MOUNT_BASE"

# Set specific permissions for NFS share
sudo -n chmod 777 "$MOUNT_BASE/nfs-share"

# 9. Install and configure NFS server
log_status "Installing NFS server..."
sudo -n apt update
sudo -n apt install -y nfs-kernel-server

# Configure NFS exports
log_status "Configuring NFS exports..."
NFS_EXPORT="$MOUNT_BASE/nfs-share *(rw,sync,no_subtree_check,no_root_squash)"

# Backup exports file
sudo -n cp /etc/exports /etc/exports.backup.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true

# Add NFS export (remove existing first)
sudo -n sed -i "\|$MOUNT_BASE/nfs-share|d" /etc/exports
echo "$NFS_EXPORT" | sudo -n tee -a /etc/exports

# Restart NFS services
log_status "Starting NFS services..."
sudo -n systemctl enable nfs-kernel-server
sudo -n systemctl restart nfs-kernel-server
sudo -n exportfs -ra

# Ensure libvirtd is running for next steps
log_status "Ensuring libvirtd service is running..."
sudo -n systemctl start libvirtd
sudo -n systemctl enable libvirtd

# 9. Test NFS mount locally
log_status "Testing NFS mount..."
TEST_MOUNT="/tmp/nfs-test"
mkdir -p "$TEST_MOUNT"

if sudo -n mount -t nfs localhost:"$MOUNT_BASE/nfs-share" "$TEST_MOUNT"; then
    log_status "NFS mount test successful"
    sudo -n umount "$TEST_MOUNT"
    rmdir "$TEST_MOUNT"
else
    log_warning "NFS mount test failed, but continuing..."
fi

# 10. Create storage info file
log_status "Creating storage configuration file..."
cat > /tmp/k8s-storage-config.yaml << EOF
# k8s Storage Configuration
# Generated on: $(date)

storage:
  device: $DEVICE_PATH
  partition: $PARTITION_PATH
  uuid: $UUID
  mount_point: $MOUNT_BASE
  
directories:
  nfs_share: $MOUNT_BASE/nfs-share
  
nfs:
  export_path: $MOUNT_BASE/nfs-share
  server_ip: localhost
EOF

sudo -n mv /tmp/k8s-storage-config.yaml "$MOUNT_BASE/k8s-storage-config.yaml"

log_status "Storage setup completed successfully!"

# 11. Display summary
echo ""
echo "=== Storage Setup Summary ==="
echo "Device: $DEVICE_PATH"
echo "Partition: $PARTITION_PATH"
echo "UUID: $UUID"
echo "Mount Point: $MOUNT_BASE"
echo "NFS Export: $MOUNT_BASE/nfs-share"
echo ""
echo "=== Available Space ==="
df -h "$MOUNT_BASE"
echo ""
echo "=== NFS Exports ==="
sudo -n exportfs -v
echo ""

log_status "Storage configuration saved to: $MOUNT_BASE/k8s-storage-config.yaml"
log_status "You can now proceed with VM setup using: ./automation/terraform/"
