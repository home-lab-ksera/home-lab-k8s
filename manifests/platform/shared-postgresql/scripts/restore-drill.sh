#!/bin/sh
# 最新の pg_dumpall を使い捨ての PostgreSQL に復元し、dump に含まれる全表の行数と一致するか確かめる。
# 本番の DB には接続しない。一時サーバーは unix socket だけで待ち受け、終了時に消える。
set -eu

backup_dir="${BACKUP_DIR:-/backup}"
work="${WORK_DIR:-/work}"
max_age_hours="${MAX_DUMP_AGE_HOURS:-48}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

dump="$(find "$backup_dir" -maxdepth 1 -name 'dumpall-*.sql.gz' | sort | tail -n 1)"
[ -n "$dump" ] || fail "no dump in $backup_dir"
age_hours=$(( ($(date +%s) - $(stat -c %Y "$dump")) / 3600 ))
echo "dump: $dump ($(stat -c %s "$dump") bytes, ${age_hours}h old)"
[ "$age_hours" -le "$max_age_hours" ] || fail "latest dump is older than ${max_age_hours}h"
gzip -t "$dump"

export PGDATA="$work/data" PGHOST="$work/run" PGUSER=postgres
rm -rf "$PGDATA" "$PGHOST"
mkdir -p "$PGHOST"

stop() { pg_ctl -m immediate stop >/dev/null 2>&1 || true; }
trap stop EXIT

initdb --username=postgres --auth=trust --encoding=UTF8 --locale=en_US.utf8 >/dev/null
pg_ctl -w -l "$work/server.log" \
  -o "-c listen_addresses='' -c unix_socket_directories=$PGHOST -c fsync=off -c full_page_writes=off" start >/dev/null

start=$(date +%s)
# dump は --clean 付き。接続中の postgres ロールは消せず作り直せないので、その 2 つの ERROR だけは想定内とする。
gunzip -c "$dump" | psql -X -q -d postgres >/dev/null 2>"$work/restore.err" || true
echo "restore: $(( $(date +%s) - start ))s"
unexpected="$(grep 'ERROR:' "$work/restore.err" \
  | grep -v -e 'ERROR:  current user cannot be dropped' -e 'ERROR:  role "postgres" already exists' || true)"
if [ -n "$unexpected" ]; then
  echo "$unexpected" >&2
  fail "restore reported errors"
fi

# dump の COPY ブロックを数え、DB・表ごとの期待行数を作る。
gunzip -c "$dump" | awk '
  /^\\connect / { db = $NF; gsub(/"/, "", db); next }
  copying && $0 == "\\." { print db "\t" table "\t" n; copying = 0; next }
  copying { n++; next }
  /^COPY .* FROM stdin;$/ { table = $2; n = 0; copying = 1 }
' > "$work/expected.tsv"

tables=0
rows=0
mismatch=0
while IFS="$(printf '\t')" read -r db table expected; do
  actual="$(psql -X -At -d "$db" -c "SELECT count(*) FROM $table")"
  if [ "$actual" != "$expected" ]; then
    echo "MISMATCH $db $table: dump=$expected restored=$actual" >&2
    mismatch=$((mismatch + 1))
  fi
  tables=$((tables + 1))
  rows=$((rows + expected))
done < "$work/expected.tsv"

[ "$tables" -gt 0 ] || fail "dump contains no tables"
[ "$mismatch" -eq 0 ] || fail "$mismatch table(s) differ"

# dump にあるデータベースがすべて存在すること。
for db in $(gunzip -c "$dump" | sed -n 's/^CREATE DATABASE \([^ ]*\) .*/\1/p'); do
  [ "$(psql -X -At -d postgres -c "SELECT count(*) FROM pg_database WHERE datname = '$db'")" = 1 ] \
    || fail "database $db is missing"
  echo "database $db: $(awk -F '\t' -v d="$db" '$1 == d { t++; r += $3 } END { printf "%d tables, %d rows", t, r }' "$work/expected.tsv")"
done

echo "OK: $tables tables, $rows rows restored and matched"
