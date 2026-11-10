# アプリ・基盤コンポーネントの更新

定期更新は [Renovate workflow](../.github/workflows/renovate.yml) が毎日 00:00 UTC（09:00 JST）と手動実行で行います。
PR を作成して検証し、**マージは手動**です。Kubernetes / containerd は [ノード更新](upgrade.md) に分けます。

## 更新経路

| 対象 | 正本と更新方法 |
|---|---|
| 公開コンテナ、bootstrap の外部 Helm chart | [renovate.json](../renovate.json) の Kubernetes / ArgoCD manager |
| PGO と関連イメージ | PGO chart を起点に同梱 chart・DB・pgBackRest・補助コンテナを同期。[専用手順](postgresql-updates.md) |
| Harbor / RustFS 同梱 chart | 手動 Application Version Updates workflow |
| ArgoCD / Gateway API の配布 URL | 手動 updater |
| private Harbor のアプリ | アプリ側 workflow。[アプリ配布](app-delivery.md) |

Renovate は charts / upstream / tasks / automation を走査対象から外し、ローカル source の `HEAD` と自作 `latest` を維持します。
GitHub Actions 自体の更新 manager は有効にしていません。
通常は公開から 3 日待ち、同時 PR 3 件・時間あたり 2 件、major は Dependency Dashboard 承認後に PR を作ります。
PGO の OCI metadata に公開日時がない場合は timestamp-optional の例外があります。

## GitHub 側の実行と検証

GITHUB_TOKEN を使い、Renovate workflow は contents / pull-requests / issues / actions の write を宣言しています。
リポジトリ設定で Actions による PR 作成を許可する必要があります。
workflow は [dispatch-renovate-validation.py](../automation/scripts/ci/dispatch-renovate-validation.py) を使い、
同一リポジトリの bot 作成 `renovate/*` PR の HEAD に `ci-deploy-validate.yml` を明示 dispatch します。
GitHub 側の必須チェック設定と、実際の最新 HEAD の検証結果を確認してからマージします。

## 手動 updater

名前は weekly ですが、[weekly-version-audit.yml](../.github/workflows/weekly-version-audit.yml) は現在 **workflow_dispatch のみ**です。
[version-audit.py](../automation/scripts/ci/version-audit.py) が候補を作成し、追跡対象の候補ツリーで validate を実行してから
`automation/update-applications` ブランチの PR を作成・更新します。

```bash
# 調査のみ（外部 registry / release 情報へアクセス）
mise exec -- python3 automation/scripts/ci/version-audit.py
# 専用ブランチで作業ツリーに候補を反映
mise exec -- python3 automation/scripts/ci/version-audit.py --apply
mise exec -- automation/scripts/ci/validate.sh
# GitHub 上で PR 作成まで実行する場合
gh workflow run weekly-version-audit.yml
```

可変タグ・private image・Kubernetes 本体の自動更新手段としては使いません。
同梱 chart の image は chart と揃え、バージョン文字列だけを書き換えて配布物を古いまま残さないようにします。

## 更新レビュー

- 描画結果の resource 名・selector・PVC・Secret 参照、CRD の変更を確認します。
- 永続データを持つサービスはバックアップと移行手順を確認します。旧タグへ戻すだけでは復旧できない場合があります。
- 同期後は Application の状態に加えて、そのサービスの認証・読み書き・既存データを確認します。
- `latest` が同じでも digest は変わります。更新 PR がないことを稼働版の固定と扱いません。

NGINX Gateway Fabric を更新する場合は、[Gateway API](../manifests/infrastructure/networking/nginx-gateway-fabric/gateway-api/) の CRD と
[NGF chart](../manifests/bootstrap/applications/infrastructure/nginx-gateway-fabric.yaml) の組み合わせを確認します。
CRD の反映を確認してから controller を更新し、Gateway / Route / ClientSettingsPolicy の condition と
api-hub / Harbor のサイズ制限も検証します。独立した Application の更新完了を wave だけで保証しません。

過去の更新結果は [archive](archive/README.md) に分離しています。
