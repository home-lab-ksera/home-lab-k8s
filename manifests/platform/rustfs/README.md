# RustFS のバケットライフサイクル

`buckets.txt` の `pdfs` と `home-camera-recordings` に、`lifecycle.json` の
30日後の期限切れルールを適用する。1か月は暦月ではなく30日として扱い、
既存オブジェクトも対象になる。実際の削除は RustFS のバックグラウンド処理で行われる。

## GitOps による反映

Kustomize が対象バケット、ルール、適用スクリプトから ConfigMap を生成する。
ArgoCD の `rustfs` Application の通常の同期が成功し、リソースが Healthy になると、
`PostSync` Job `rustfs-lifecycle` が既存の `rustfs-auth` Secret で S3 API に接続する。
認証情報は Git に保存しない。NetworkPolicy は同じ namespace の設定 Job から
RustFS の TCP/9000 への通信を許可する。

Job はバケットごとに既存設定を取得し、ID `expire-all-objects-after-30-days` の
ルールのみを置換する。他の ID のルールは保持し、適用後に読み戻して検証する。
既存設定がない場合のみ新規作成し、認証・通信エラーは失敗として扱う。
対象バケットは事前に存在する必要があり、Job はバケットを作成しない。

保持日数の変更は `lifecycle.json` の `Expiration.Days` を編集する。
管理対象ルールは1件で、更新のため ID は固定する（ID の `30-days` は変更しない）。
対象の変更は `buckets.txt` を編集し、Git へ反映後に ArgoCD で同期する。
ルールの無効化は `Status` を `Disabled` にして同期してから対象の除外を行う。
ファイル、バケット名、Job を Git から削除するだけでは、S3 内のルールは削除されない。

S3 内の手動変更は ArgoCD の差分検知対象外であり、次の通常同期で再適用される。
Selective Sync では Hook が動かないため、`rustfs` Application 全体を同期する。
成功・失敗した Job はログ確認のため残し、次の同期時に `BeforeHookCreation` で再作成する。

```bash
kubectl -n rustfs logs job/rustfs-lifecycle
```

このルールは現行バージョンの期限切れを設定する。現在の2バケットは
バージョニング無効だが、将来有効化する場合は旧バージョンの保持・削除方針も別途定義する。

参考: [ArgoCD Sync Phases and Waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/)

viewer用CORSは `buckets.txt` の両バケットへ適用する。内部S3経路は録画バケットのGET/HEAD/OPTIONSにも対応し、api-hubが発行する署名で再生する。
