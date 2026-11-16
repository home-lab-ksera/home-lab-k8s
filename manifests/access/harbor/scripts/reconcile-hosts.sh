#!/bin/sh
# ノードの名前解決を内部 Gateway に固定する。containerd の再起動は不要。
# 旧名 harbor.qroksera.com の行は取り除く（2026-11-16 に廃止）。
set -eu
hosts_file=${HOSTS_FILE:-/host/hosts}
while :; do
  temporary=$(mktemp)
  awk '
    /^[[:space:]]*#/ { print; next }
    {
      original = $0
      comment = ""
      if (match($0, /#/)) {
        comment = substr($0, RSTART)
        $0 = substr($0, 1, RSTART - 1)
      }
      changed = 0
      kept = $1
      count = 0
      for (i = 2; i <= NF; i++) {
        if ($i == "harbor.qroksera.com" || $i == "harbor.internal.qroksera.com") {
          changed = 1
        } else {
          kept = kept " " $i
          count++
        }
      }
      if (!changed) print original
      else if (count) print kept (comment == "" ? "" : " " comment)
      else if (comment != "" && comment != "# managed: harbor-node-hosts") print comment
    }
    END { print "192.168.122.100 harbor.internal.qroksera.com # managed: harbor-node-hosts" }
  ' "$hosts_file" > "$temporary"
  if ! cmp -s "$temporary" "$hosts_file"; then
    # hostPath のファイルマウントに対して rename せず、既存 inode を更新する。
    cat "$temporary" > "$hosts_file"
    echo 'Harbor のノード名前解決を内部 Gateway に更新しました'
  fi
  rm -f "$temporary"
  if [ "${RECONCILE_ONCE:-false}" = true ]; then exit 0; fi
  sleep 60 &
  wait "$!"
done
