# Harbor の保持と GC

イメージの整理は Harbor 標準の保持ルールと GC だけで行います。専用の保守 Job・独自タグ
（`deploy` / `retain-current` / `retain-previous`）・`maintenance.json` は 2026-11-16 に撤去しました。

## 設定（Harbor の DB に保存。Git 管理外）

| 項目 | 値 |
|---|---|
| 保持ルール（project `sandbox`） | 全 repository で「直近に push された 5 件の artifact を保持」。毎日 03:00 JST に実行 |
| 保持の例外 | `cooklog-slack` は全件保持。manifest が特定の `build-*` タグに固定されており、Image Updater の対象外のため |
| GC | 毎日 03:30 JST、`delete_untagged=false`、workers=1 |
| quota（project `sandbox`） | 4 GiB。到達すると push が失敗する |

Harbor を作り直した場合は、UI（Projects → sandbox → Policy、Administration → Clean Up）で同じ値を設定します。
API で設定する場合の例:

```bash
# project の retention_id を確認し、なければ POST /retentions、あれば PUT /retentions/<id>
curl -su "admin:$HARBOR_ADMIN_PASSWORD" https://harbor.internal.qroksera.com/api/v2.0/projects/sandbox | jq .metadata.retention_id
```

稼働中のイメージは、そのリポジトリの直近 5 件に含まれている限り削除されません。
5 世代より前の版へ戻したい場合は、アプリのリポジトリを revert して CI で再ビルドします。

## 配布と切り戻し

CI が default branch の HEAD を `latest` として公開し、Image Updater が `latest` の digest を Git へ書き戻します
（[アプリ配布](app-delivery.md)）。前の配布の成否は待ちません。

切り戻しは次のどちらかで行います。`kustomization.yaml` の digest だけを revert しても、Image Updater が
約 2 分で `latest` の digest に戻します。

1. アプリのリポジトリで原因の commit を revert し、CI に公開させる（通常の手順）。
2. 急ぐ場合は Harbor の UI で、現在の artifact から `latest` タグを外し、戻したい artifact に `latest` を付ける。

脆弱性スキャンは Harbor ではなく、各アプリの公開 workflow が push 前のイメージに対して行います。

## 確認

```bash
kubectl -n argocd logs deployment/argocd-image-updater-controller --tail=50
kubectl -n sandbox get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[*].imageID}{"\n"}{end}'
```

保持と GC の実行履歴は Harbor の UI（Projects → sandbox → Policy、Administration → Clean Up → History）で確認します。
