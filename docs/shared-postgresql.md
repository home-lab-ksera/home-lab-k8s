# 共通 PostgreSQL の運用

[PGO](../manifests/platform/pgo/) が [PostgresCluster](../manifests/platform/shared-postgresql/postgrescluster.yaml) を管理します。
Namespace は `database`、名前は `shared-postgresql`、PostgreSQL major は 18、DB instance は 1 replica です。
版番号と関連イメージの更新は [PostgreSQL 更新](postgresql-updates.md) を参照してください。

## 接続と権限

| 項目 | 宣言 |
|---|---|
| primary | `shared-postgresql-primary.database.svc:5432` |
| 利用 DB / role | `registry`, `api_hub`, `hitomi`, `media`（cameraは退役履歴を保持） / 共用 `app` |
| 管理 role | `postgres` |
| TLS | アプリは PGO の公開 CA を使い `verify-full` |
| 接続数 | DB 全体 100、app 80、Harbor 各 Pod 最大 10、Grafana datasource 最大 5 |

[bootstrap.sql](../manifests/platform/shared-postgresql/scripts/bootstrap.sql) は現行4 DBの所有権を app に設定し、既存camera履歴DBがある場合は同じ権限を維持する。
PUBLIC の各アプリDBの CONNECT / TEMP、postgres CONNECT を取り消します。
共用 role はアプリごとのデータ分離を提供しません。接続先を追加する際は DB / schema の権限と接続予算を見直します。

アプリ主処理のpool上限はapi-hub API/配送共有3、media API3/worker2/planner2、hitomi収集2/outbox2で合計14（変更前18）。cameraはDB接続しない。
API・media worker・outboxはRecreate、cameraは単一StatefulSet。media APIのrollout時に3接続が一時追加され17、3つのmigration Job各1接続を含め20。
明示media import2・毎時reconcile2・資格情報Jobのapp確認2（定期実行と同期Hookが重なる場合）・Harbor rollout40・Grafana5を加えた最大見積りは71。app上限80/クラスタ100を維持し、appの残りは9接続。追加Job/replicaはこの予算に含め直す。
2026-10-24のapp接続3（idle）/DBコンテナ約368MiBは過去の待機時の値であり、新構成のピーク実測とは扱わない。

sandboxでは `application-postgresql` SecretのPGPASSWORDを使い、Podに `database.k8s-myhome.local/shared-postgresql: "true"` を付けてNetworkPolicyを通す。
`shared-postgresql-ca` の `ca.crt` をread-only mountし、PGHOSTは上記Service、PGUSERはapp、PGDATABASEは担当DB、PGSSLROOTCERTはmount先を指定する。
Secretの入力は既存の `postgresql.password` のみで、外部Secret管理の項目は増やさない。

## 資格情報の反映

ESC の `postgresql.admin_password` / `postgresql.password` を ESO が入力 Secret に配布します。
[postgresql-credentials](../manifests/platform/shared-postgresql/credentials.yaml) は 30 分ごとに動き、
[reconcile.py](../manifests/platform/shared-postgresql/scripts/reconcile.py) が次を実行します。

1. 入力が変わった場合だけ PGO の postgres / app Secret の password を patch し verifier を解除。
2. PGO の公開 CA を harbor / observability / sandbox の `shared-postgresql-ca` ConfigMap に配布。
3. 管理接続で bootstrap SQL を実行し、app / registry への TLS 接続を確認。

定期実行は最大720回/日から48回/日へ減らす。初期構築で次の定期実行を待たないよう、
ArgoCD の通常同期後にも `postgresql-credentials-sync` PostSync Job で同じ処理を1回実行する。
Job の spec は Kustomize が CronJob の template からコピーし、イメージ・設定・権限の差分を防ぐ。
Selective Sync は Hook を実行しないため、初期反映は Application 全体を同期する。

PGO の生成 Secret は ESO の所有物に変更しません。Job は復元マーカーを作成・変更しません。
資格情報ローテーションは利用アプリの停止、ESC 更新、ESO と Job の成功、再接続の順に計画します。
Harbor は現行 Reloader の監視範囲外なので、Secret 更新だけで自動再起動するとは扱いません。

ローテーション時は `postgresql-input` への ESO 反映後、定期実行を待たずに同じ template から Job を作れる。
通常同期の Hook が成功していれば追加実行は不要。既存の資格情報 Job が実行中なら終了を待ち、
手動 Job は1つずつ実行する。CronJob の Forbid は Hook / 手動 Job との重複を防がない。

```bash
credentials_job="postgresql-credentials-manual-$(date -u +%Y%m%d%H%M%S)"
kubectl -n database create job "$credentials_job" --from=cronjob/postgresql-credentials
kubectl -n database wait --for=condition=complete "job/$credentials_job" --timeout=360s
kubectl -n database logs "job/$credentials_job"
```

失敗時は Job / Pod の状態とログを確認し、利用側の再開は成功確認後に行う。
定期 Job には最大30分の待ち時間がある。上記の即時実行と、PGO の反映完了・利用側の再接続は別に確認する。

## 管理端末から接続する

`kubectl` と psql を用意します。公開 CA を取得します（秘密鍵・パスワードは出力しません）。

```bash
mkdir -p "$HOME/.postgresql"
chmod 700 "$HOME/.postgresql"
kubectl -n harbor get configmap shared-postgresql-ca \
  -o jsonpath='{.data.ca\.crt}' > "$HOME/.postgresql/shared-postgresql-ca.crt"
chmod 600 "$HOME/.postgresql/shared-postgresql-ca.crt"
```

現在の primary Pod を選んで転送します。PGO の primary Service の selector に依存しないよう Pod を指定します。

```bash
postgresql_primary_pod="$(kubectl -n database get pods \
  -l 'postgres-operator.crunchydata.com/cluster=shared-postgresql,postgres-operator.crunchydata.com/role=master' \
  -o jsonpath='{.items[0].metadata.name}')"
kubectl -n database port-forward "pod/$postgresql_primary_pod" 15432:5432 --address 127.0.0.1
```

別ターミナルで接続し、ESC の `postgresql.password` を入力します。

```bash
psql "host=shared-postgresql-primary.database.svc hostaddr=127.0.0.1 port=15432 dbname=registry user=app sslmode=verify-full sslrootcert=$HOME/.postgresql/shared-postgresql-ca.crt connect_timeout=10" -W
```

`hostaddr` は転送先、`host` は証明書検証の名前です。primary 変更時は Pod 選択からやり直します。
接続後は `SELECT current_database(), current_user;` と
`SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid();` で確認できます。

## バックアップ

DB PVC（instance set `main-local`）は 10Gi の `local-path` で、k8s-worker2 の VM ディスクに固定されます。
pgBackRest repo1 は 20Gi の `nfs-postgresql` / Retain で、NFS の宣言先は `192.168.122.1:/mnt/k8s-storage/nfs-share` です。
DB は VM ディスク、backup はホストの HDD RAID1 にあり、片方の喪失はもう片方から戻せます。
`local-path` の reclaim policy は Delete のため、PVC を消すと DB の実データも消えます。

PostgresCluster は full `0 18 * * 0`、differential `0 18 * * 1-6`、full 3 世代保持を指定しています。
CR には timezone 指定がないため、生成 CronJob と controller のタイムゾーンを確認します。
UTC 解釈の場合は full が月曜 03:00 JST、differential が火〜日曜 03:00 JST です（2026-11-10 に実行時刻から UTC 解釈を確認）。
CronJob の成功時刻を `PostgreSQLBackupStale`（26時間成功なし）と `PostgreSQLBackupFailed`（直近実行の失敗）で監視します。
Job の成功は WAL アーカイブの継続や復元可能性までは保証しません。

```bash
# 新しい full backup を実行する操作
kubectl -n database annotate postgrescluster shared-postgresql --overwrite \
  postgres-operator.crunchydata.com/pgbackrest-backup="$(date -u +%Y%m%dT%H%M%SZ)"
kubectl -n database get jobs,pods
kubectl -n database exec shared-postgresql-repo-host-0 -c pgbackrest -- \
  pgbackrest --stanza=db info
```

Job 成功と backup の識別子・成功時刻を照合します。資格情報 Job の成功は backup 成功ではありません。
別クラスタへの復元例は [restore-test.yaml](../automation/examples/postgresql/restore-test.yaml) です。
通常の GitOps 対象ではなく、source / namespace / volume を確認して検証用に使う定義です。

## 再起動と再構築

既存 PVC を保持した Pod / VM 再起動と、PV/PVC の情報も失うクラスタ再作成を区別します。
Terraform の VM CPU mode は `host-model` です。

再構築前に PV の NFS server/path、PVC/volumeName、DB 世代、backup 一覧、ESC などの認証情報を保全します。
DB の実データは worker2 の VM ディスクにあり、VM を削除すると失われます。
`make phase2` や worker2 の作り直しの前に、手動 full backup を実行し、`pgbackrest info` で成功を確認します（[バックアップ](#バックアップ)）。
復旧は repo1 からの復元になります。
**NFS の旧ファイルが存在するだけでは、新クラスタが自動的にそこへ接続するとは限りません。**

1. Harbor など利用側の書き込みを停止し、GitOps が復旧設定を戻さない状態にします。
2. NFS と旧 DB / repo の実パス・内容、旧プロセスの停止を確認します。
3. ESO / PGO を準備し、保持した PV/PVC の再接続か、backup から別領域への復元を選びます。
   空の動的 PVC を旧データと取り違えず、同じ repo を新しい空 DB 用に初期化しません。
4. 対象 backup / PVC を明示した復旧設定を、同梱 PGO CRD と照合して用意します。
   旧 UID / resourceVersion / claimRef.uid をそのまま新クラスタへコピーしません。
5. 復元後、TLS と app の認証、テーブル・行・シーケンス・世代、所有権と接続権限を確認します。
6. Harbor の復元ゲートを確認してから利用側を再開し、既存 image の pull と検証用 push を確認します。

[Harbor の待機スクリプト](../manifests/platform/harbor/scripts/wait-for-database.sh) は
`platform_bootstrap.migration` の `application='harbor'` に対する generation が
[patch の MIGRATION_GENERATION](../manifests/platform/harbor/harbor-database-patch.yaml) と一致するまで待ちます。
現在の指定は `harbor-pgo-20261019` です。空 DB の自動初期化・復元済み判定は実装されていません。
マーカーは実データ検証後の判定に使い、待機を迂回するために作成しません。

旧 `harbor` role のバックアップを戻す場合は app への所有権移管と、復元マーカーを読む権限も確認します。

## Grafana から SQL を使う

`04 PostgreSQL / SQL` ダッシュボードと Explore の `PostgreSQL / registry` を使います。
[datasources](../manifests/platform/observability/config/grafana-datasources.yaml) の接続 role は **読み取り専用ではなく共用 app** です。
実行する SQL の影響を確認してください。表示する DB 統計は問い合わせ時点の値です。

資格情報 Job が配布した CA と ESO の password で接続します。
Harbor の rolling update 最大40、Grafana 5も共用appの予算に含めます。上記80接続の枠はアプリ追加時に再評価します。

cameraの退役DBは削除しない。PGOの宣言から除外しても実DBは自動削除されない（[公式仕様](https://access.crunchydata.com/documentation/postgres-operator/latest/tutorials/basic-setup/user-management)）。bootstrapは既存camera DBだけの権限を保持し、新規導入時に作成を要求しない。過去のSQLx migrationは変更しない。
