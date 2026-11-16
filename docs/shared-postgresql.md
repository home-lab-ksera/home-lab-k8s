# 共通 PostgreSQL の運用

公式イメージの単一 [StatefulSet](../manifests/platform/shared-postgresql/postgresql.yaml)（`database/postgresql`）です。
operator は使いません（PGO・pgBackRest・資格情報 Job は 2026-11-16 に撤去）。PostgreSQL major は 18、replica は 1 です。

## 接続と権限

| 項目 | 宣言 |
|---|---|
| 接続先 | `postgresql.database.svc:5432` |
| 利用 DB / role | `registry`, `api_hub`, `hitomi`, `media`（camera は退役履歴を保持） / 共用 `app` |
| 管理 role | `postgres` |
| TLS | サーバー証明書は cert-manager のクラスタ内 CA が発行。アプリは `verify-full` |
| 接続数 | DB 全体 100、app 80、Harbor 各 Pod 最大 10、Grafana datasource 最大 5 |

共用 role はアプリごとのデータ分離を提供しません。接続先を追加する際は DB / schema の権限と接続予算を見直します。

アプリ主処理の pool 上限は api-hub 3、media API 3 / worker 2 / planner 2、hitomi 収集 2 / outbox 2 で合計 14。
rollout・migration Job・明示 import・Harbor rollout 40・Grafana 5 を加えた最大見積りは 71 で、app 上限 80 に収まります。
追加の Job / replica はこの予算に含め直します。

sandbox では `application-postgresql` Secret の PGPASSWORD を使い、Pod に
`database.k8s-myhome.local/shared-postgresql: "true"` を付けて NetworkPolicy を通します。
CA は Secret `postgresql-ca` の `ca.crt` を read-only mount し、PGHOST は上記 Service、PGUSER は app、
PGDATABASE は担当 DB、PGSSLROOTCERT は mount 先を指定します。

## 証明書

[postgresql-certificates.yaml](../manifests/infrastructure/security/cert-manager/postgresql-certificates.yaml) が次を作ります。

- `cert-manager/postgresql-ca`: クラスタ内専用の CA（10 年）。
- `database/postgresql-server-tls`: サーバー証明書（1 年、30 日前に更新）。更新時は Reloader が StatefulSet を再起動します。
- `harbor` / `observability` / `sandbox` の `postgresql-ca`: 同じ CA が発行した証明書。利用側はこの Secret の `ca.crt` だけを使います。

CA を配る Job はありません。利用する namespace を増やすときは、同ファイルに Certificate を 1 つ追加します。

## 初期化と資格情報

空のデータディレクトリで起動したときだけ、[init-roles.sh](../manifests/platform/shared-postgresql/scripts/init-roles.sh) が
`app` role と 4 つの DB を作り、[bootstrap.sql](../manifests/platform/shared-postgresql/scripts/bootstrap.sql) が所有権と接続権限を設定します。
パスワードは ESC の `postgresql.admin_password` / `postgresql.password`（ESO の `postgresql-input`）です。

既存データがある場合、Secret を変えても DB 内のパスワードは変わりません。ローテーションは次の順で行います。

```bash
kubectl -n database exec -it postgresql-0 -- psql -U postgres -c '\password app'   # ESC の新しい値を入力
# ESC を更新 → ExternalSecret を force-sync（docs/secrets.md）→ 利用側を再起動
```

DB を追加するときは `init-roles.sh` と `bootstrap.sql` に書き足し、既存環境には同じ SQL を手で実行します。

## 管理端末から接続する

```bash
kubectl -n database exec -it postgresql-0 -- psql -U postgres            # Pod 内（unix socket）
kubectl -n database port-forward pod/postgresql-0 15432:5432 --address 127.0.0.1
kubectl -n sandbox get secret postgresql-ca -o jsonpath='{.data.ca\.crt}' | base64 -d > /tmp/postgresql-ca.crt
psql "host=postgresql.database.svc hostaddr=127.0.0.1 port=15432 dbname=registry user=app sslmode=verify-full sslrootcert=/tmp/postgresql-ca.crt" -W
```

## バックアップ

DB の PVC は 10Gi の `local-path` で、k8s-worker2 の VM ディスクに固定されます。reclaim policy は Delete のため、PVC を消すと実データも消えます。
[postgresql-backup](../manifests/platform/shared-postgresql/backup.yaml) が毎日 03:00 JST（18:00 UTC）に `pg_dumpall` を
`nfs-postgresql`（Retain、ホストの HDD RAID1）の PVC へ書き、7 日分を残します。
成功が 26 時間ない場合は `PostgreSQLBackupStale` が通知します。特定時点への復元（PITR）はできません。

```bash
kubectl -n database create job "postgresql-backup-manual-$(date -u +%Y%m%d%H%M%S)" --from=cronjob/postgresql-backup
kubectl -n database get jobs
```

## 復元

1. 利用側（api-hub、hitomi、media、Harbor）の書き込みを止めます。
2. バックアップの PVC を mount した Pod か、ホストの NFS 上のファイルから、対象の dump を選びます。
3. 復元します。dump は `--clean --if-exists` 付きで、既存の DB を作り直します。

   ```bash
   gunzip -c dumpall-<日時>.sql.gz | kubectl -n database exec -i postgresql-0 -- psql -U postgres -d postgres
   ```

4. テーブル・行数・所有権を確認し、利用側を再開します。

VM やクラスタを作り直す場合は、先に手動バックアップを実行して成功を確認します。新しい空の DB が起動した後、同じ手順で戻します。

[Harbor の待機スクリプト](../manifests/platform/harbor/scripts/wait-for-database.sh) は、`registry` DB の
`platform_bootstrap.migration` に [patch の MIGRATION_GENERATION](../manifests/platform/harbor/harbor-database-patch.yaml) と同じ
generation があるまで Harbor を起動しません。空の DB で Harbor が初期化を始めるのを防ぐためで、マーカーは dump に含まれます。
データなしで新規に始める場合だけ、このテーブルと行を手で作ります。

## Grafana から SQL を使う

`04 PostgreSQL / SQL` ダッシュボードと Explore の `PostgreSQL / registry` を使います。
[datasources](../manifests/platform/observability/config/grafana-datasources.yaml) の接続 role は **読み取り専用ではなく共用 app** です。
実行する SQL の影響を確認してください。

## 移行記録（2026-11-16）

PGO 管理のクラスタ（Crunchy 18.6）から移行しました。旧 DB を読み取り専用にして DB ごとに `pg_dump` → `pg_restore` し、
全テーブルの行数一致（registry 50 表、api_hub 2、hitomi 6、media 9、camera 2）を確認してから接続先を切り替えました。
Crunchy 固有の `pgaudit` 拡張は移していません。移行時の dump は切り替えの確認後に削除しました。
