# PGO

Crunchy Postgres Operator 6.0.3 の Helm chart を同梱する。
取得元は `oci://registry.developers.crunchydata.com/crunchydata/pgo:6.0.3`、
取得時の chart digest は `sha256:0d27d9923c7393a77e1d0b9e31344d30873b1ca2c513f0f93d62764801eec7a2`。

Application `pgo`（wave 8）が Operator と CRD を所有する。
Operator は `postgres-operator` に配置し、`PGO_TARGET_NAMESPACE=database` で監視範囲を限定する。
chart の RBAC はクラスタ範囲であり、監視範囲の限定と RBAC の制限は同義ではない。
CRD の prune / delete は確認必須とし、DB の連鎖削除を防ぐ。

共有 DB は `manifests/platform/shared-postgresql/` で管理する。Harbor は共通 DB を使用する。
UBI 9 の x86-64-v2 要件に対応するため、VM の CPU mode は `host-model` とする。
`host-passthrough` では worker1 の libc segfault と停止を観測したため、互換 CPU モデルへ変更し、両ワーカーのコールド起動を確認済み。Operator は `replicas: 1` で運用する。
運用・バックアップ・再構築・資格情報の手順は [共通 PostgreSQL](../../../docs/shared-postgresql.md) を参照する。

```bash
kubectl -n postgres-operator rollout status deployment/pgo
kubectl get crd postgresclusters.postgres-operator.crunchydata.com
kubectl -n database get postgresclusters
```

更新時は chart と対応する PostgreSQL / pgBackRest イメージを一緒に確認し、
`automation/scripts/ci/validate.sh` と API server の dry-run を実施する。
