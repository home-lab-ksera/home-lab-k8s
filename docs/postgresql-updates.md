# PostgreSQL の更新

[共通 PostgreSQL](shared-postgresql.md) は公式イメージ `postgres:<major>.<minor>` を使います。
同じタグを StatefulSet、バックアップ CronJob、Harbor の待機用 init container（2 か所）で使います。

## minor 更新

Renovate が 4 か所をまとめた PR（グループ名 `PostgreSQL`）を作ります。自動マージはしません。
マージすると StatefulSet が再起動し、数十秒の接続断が起きます。反映後に確認します。

```bash
kubectl -n database rollout status statefulset/postgresql
kubectl -n database exec postgresql-0 -- psql -U postgres -Atc 'show server_version'
```

## major 更新

データディレクトリに互換性がないため、Renovate では無効にしています。手順は次のとおりです。

1. 手動バックアップを実行して成功を確認する（[バックアップ](shared-postgresql.md#バックアップ)）。
2. 利用側の書き込みを止め、最新の dump を取る。
3. イメージのタグを上げ、StatefulSet と PVC `data-postgresql-0` を削除して空の DB で起動させる。
4. dump を復元し、行数と所有権を確認して利用側を再開する（[復元](shared-postgresql.md#復元)）。
