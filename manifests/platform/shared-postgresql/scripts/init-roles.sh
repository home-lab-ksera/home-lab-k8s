#!/bin/sh
# 空のデータディレクトリを初期化するときだけ実行される。共用の app role と各アプリの DB を作る。
set -eu
psql -X -v ON_ERROR_STOP=1 --username postgres -v app_password="$APP_PASSWORD" <<'SQL'
CREATE ROLE app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 80 PASSWORD :'app_password';
SELECT format('CREATE DATABASE %I OWNER app', name) FROM unnest(ARRAY['registry', 'api_hub', 'hitomi', 'media']) AS name \gexec
SQL
