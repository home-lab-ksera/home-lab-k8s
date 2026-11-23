# PostgreSQL

公式イメージをそのまま使う、単一の [StatefulSet](../manifests/platform/shared-postgresql/postgresql.yaml)（`database/postgresql`、replica 1）です。
operator は使っていません。`k8s-worker2` に固定され、データは local-path の PVC にあります。

## 接続

| 項目 | 値 |
|---|---|
| 接続先 | `postgresql.database.svc:5432` |
| DB | `registry`（Harbor）、`api_hub`、`hitomi`、`media`、`market`。`camera` は使っていない古い DB |
| role | アプリ共用の `app`（接続上限 80）、管理用の `postgres` |
| TLS | 必須。クライアントは `verify-full` で接続する |
| 接続できる Pod | database 内、Harbor、Grafana、sandbox でラベル `database.k8s-myhome.local/shared-postgresql: "true"` を持つ Pod |

`app` は全 DB の所有者で、アプリ同士のデータは分離されていません。Grafana のデータソースも同じ `app` で、読み取り専用ではありません。

### アプリから使う

sandbox のアプリは次を使います（既存のアプリのマニフェストを写すのが早いです）。

- ConfigMap `application-postgresql`: `PGHOST`、`PGUSER`、`PGSSLROOTCERT`
- Secret `application-postgresql`: `PGPASSWORD`
- Secret `postgresql-ca` の `ca.crt` を `/etc/postgresql/ca.crt` に read-only で mount
- `PGDATABASE` に自分の DB 名、Pod にラベル `database.k8s-myhome.local/shared-postgresql: "true"`

### 接続数

サーバー全体で 100、`app` で 80 が上限です。現在の割り当ては次のとおりです（各マニフェストの `PGPOOL_MAX`）。

| 利用者 | 上限 |
|---|---|
| api-hub | 3 |
| media API / worker / 定期 Job | 3 / 2 / 各 2 |
| hitomi 収集 / outbox | 2 / 2 |
| market-collector（sp500 / nikkei225） | 各 1 |
| migration Job | 各 1 |
| Harbor | Pod ごとに 10 |
| Grafana（registry / market） | 5 / 2 |

Harbor の rollout 中は新旧の Pod が同時に接続します。replica や Job を足すときは、合計が 80 に収まるか確認します。

### 管理端末から

```bash
kubectl -n database exec -it postgresql-0 -- psql -U postgres            # Pod の中から

kubectl -n database port-forward pod/postgresql-0 15432:5432 --address 127.0.0.1
kubectl -n sandbox get secret postgresql-ca -o jsonpath='{.data.ca\.crt}' | base64 -d > /tmp/postgresql-ca.crt
psql "host=postgresql.database.svc hostaddr=127.0.0.1 port=15432 dbname=registry user=app sslmode=verify-full sslrootcert=/tmp/postgresql-ca.crt" -W
```

Grafana では、ダッシュボード「04 PostgreSQL / SQL」と Explore の `PostgreSQL / registry` から SQL を実行できます。

## 証明書

[postgresql-certificates.yaml](../manifests/infrastructure/security/cert-manager/postgresql-certificates.yaml) が cert-manager で次を作ります。

| Secret | 内容 |
|---|---|
| `cert-manager/postgresql-ca` | クラスタ内専用の CA（10 年） |
| `database/postgresql-server-tls` | サーバー証明書（1 年、期限の 30 日前に更新） |
| `harbor`・`observability`・`sandbox` の `postgresql-ca` | 同じ CA が発行した証明書。クライアントはこの `ca.crt` だけを使う |

DB を使う namespace を増やすときは、同じファイルに Certificate を 1 つ足します。

サーバー証明書が更新されると、[Reloader](secrets.md#reloader) が PostgreSQL を再起動して新しい証明書を読み込ませます（年 1 回、数十秒つながらなくなります）。
再起動されたかは次で確かめられます。

```bash
kubectl -n database get certificate postgresql-server -o jsonpath='{.status.notBefore}{"\n"}'
kubectl -n database get pod postgresql-0 -o jsonpath='{.status.startTime}{"\n"}'   # notBefore より後なら読み込み済み
```

Reloader が止まっていて再起動されなかった場合は、30 日以内に手で `kubectl -n database rollout restart statefulset postgresql` を実行します。
そうしないと、古い証明書の期限切れでクライアントが接続できなくなります。

## 初期化

データディレクトリが空のときだけ、[init-roles.sh](../manifests/platform/shared-postgresql/scripts/init-roles.sh) が `app` と 5 つの DB を作り、
[bootstrap.sql](../manifests/platform/shared-postgresql/scripts/bootstrap.sql) が所有権と接続権限を設定します。
パスワードは Secret `postgresql-input`（ESC の `postgresql.admin_password` / `postgresql.password`）です。

DB を足すときは、この 2 つのファイルに書き足し、稼働中の DB には同じ SQL を手で実行します。

### パスワードを変える

Secret を変えても、既存の DB の中のパスワードは変わりません。次の順で行います。

```bash
kubectl -n database exec -it postgresql-0 -- psql -U postgres -c '\password app'   # 新しい値を入力
```

1. 上のコマンドで DB の中のパスワードを変える。
2. ESC の `postgresql.password` を同じ値にする。
3. ExternalSecret を [force-sync](secrets.md#値を変える) する（`postgresql-input`、`application-postgresql`、`harbor-database-secret`、`grafana-postgresql`）。
4. sandbox のアプリと Grafana は Reloader が再起動する。Harbor は手で再起動する。

1 から 4 の間は、アプリが DB に接続できません。

## バックアップ

CronJob [postgresql-backup](../manifests/platform/shared-postgresql/backup.yaml) が毎日 03:00 JST に `pg_dumpall` を取り、
NFS の PVC `postgresql-backup` に `dumpall-<UTC の日時>.sql.gz` として置きます。7 日分を残します。

- 26 時間成功していないと `PostgreSQLBackupStale` が通知します。
- 特定の時刻への復元（PITR）はできません。失うのは最大で 1 日分です。
- dump は DB と同じホストにあります。ホストの HDD ごと失うと戻せません。

手動で取る:

```bash
kubectl -n database create job "postgresql-backup-manual-$(date -u +%Y%m%d%H%M%S)" --from=cronjob/postgresql-backup
kubectl -n database get jobs
```

dump のファイルは、ホストの `/mnt/k8s-storage/nfs-share/database-postgresql-backup-pvc-<uid>/` にあります。

## 復元訓練

CronJob [postgresql-restore-drill](../manifests/platform/shared-postgresql/restore-drill.yaml) が毎週日曜 04:30 JST に、
最新の dump を Pod 内の使い捨て PostgreSQL に戻して確かめます（[スクリプト](../manifests/platform/shared-postgresql/scripts/restore-drill.sh)）。
本番の DB には接続せず、dump は読み取り専用で使います。

- 失敗とする条件: dump が無い、48 時間より古い、gzip が壊れている、復元で想定外の `ERROR` が出た、
  dump の `COPY` の行数と復元後の `count(*)` が 1 表でも違う、dump にあるデータベースが無い。
- 想定内の `ERROR` は、接続中の `postgres` ロールを消せない・作り直せないという 2 つだけです。
- 失敗は `KubernetesCronJobFailed`、8 日間成功していなければ `PostgreSQLRestoreDrillStale` が通知します。
- Job のログに、データベースごとの表と行数、復元にかかった秒数が出ます。

手動で実行する:

```bash
kubectl -n database create job "postgresql-restore-drill-manual-$(date -u +%Y%m%d%H%M%S)" --from=cronjob/postgresql-restore-drill
kubectl -n database logs -f job/<作った Job の名前>
```

訓練が確かめるのは「dump を空の PostgreSQL に戻せること」までです。下の手順で使うアプリ側の確認は含みません。

## 復元

1. DB に書き込むものを止める（api-hub、hitomi-outbox、media-api、media-worker、Harbor。CronJob は suspend）。
   ArgoCD の selfHeal が戻すため、対象の Application の自動同期を一時的に止めるか、Git で replica を 0 にします。
2. 使う dump を選ぶ。
3. 復元する。dump は `--clean --if-exists` 付きで、既存の DB を作り直します。

   ```bash
   gunzip -c dumpall-<日時>.sql.gz | kubectl -n database exec -i postgresql-0 -- psql -U postgres -d postgres
   ```

4. テーブルと行数を確認し、止めたものを戻す。

DB が dump の時点に戻るため、その後に送った通知がもう一度送られることがあります。これは許容しています。
アプリ側の確認は [アプリ一覧](apps.md#db-を復元したあと) を参照してください。

### Harbor の起動ゲート

Harbor の core と jobservice は、[待機スクリプト](../manifests/platform/harbor/scripts/wait-for-database.sh) が
`registry` DB の `platform_bootstrap.migration` に、[patch の `MIGRATION_GENERATION`](../manifests/platform/harbor/harbor-database-patch.yaml)
と同じ行を見つけるまで起動しません。空の DB で Harbor が勝手に初期化を始めるのを防ぐためです。

- 待機中は init container のログに `Waiting for PostgreSQL credentials, TLS, and verified Harbor restore` と出ます。
- この行は dump に含まれるため、復元すれば通ります。
- Harbor を空から始める場合だけ、行を手で作ります。

  ```sql
  -- registry DB で実行
  CREATE SCHEMA IF NOT EXISTS platform_bootstrap;
  CREATE TABLE IF NOT EXISTS platform_bootstrap.migration (
    application text NOT NULL, generation text NOT NULL, confirmed_at timestamptz NOT NULL DEFAULT now());
  INSERT INTO platform_bootstrap.migration (application, generation) VALUES ('harbor', '<MIGRATION_GENERATION の値>');
  GRANT USAGE ON SCHEMA platform_bootstrap TO app;
  GRANT SELECT ON platform_bootstrap.migration TO app;
  ```

## 版を上げる

minor は Renovate の PR、major は dump と復元が要ります。[更新](updates.md#postgresql) を参照してください。
