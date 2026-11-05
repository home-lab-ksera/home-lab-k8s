# アプリの追加と配布

アプリ実体は [apps/](../manifests/apps/)、公開経路は [access/](../manifests/access/) に置きます。
Application の namespace と source は [一覧](diagrams/app-of-apps-sync-wave.md) が参照先です。
現在のユーザーアプリは api-hub、blog、cooklog、hitomi、hitomi-pdf、hitomi-upload-viewer、home-camera、rsshub、nature-remo。
すべて `sandbox` に配置します。Selenium は独立アプリではなく、hitomi の収集 Job 内でのみ起動します。
起動待ち・終了・切替手順は [Selenium の非常駐化](hitomi-selenium-sidecar.md) を参照してください。
Nature Remo は [ローカル操作アプリの手順](nature-remo.md) に従って固定 digest を Git 反映します。Image Updater の対象には含めません。

## 新規追加

1. `manifests/apps/<app>/` に workload / Service と `kustomization.yaml` を作成します。
2. `bootstrap/applications/user-apps/<app>-app.yaml` を作成し、同ディレクトリの Kustomization に登録します。
   既存例の Project `apps`、Git revision `HEAD`、wave 12 を基準にします。
3. 必要な Namespace / AppProject destination、共有設定、ExternalSecret、PVC を用意します。
4. hostname を公開する場合は [access 手順](access.md#新しく到達可能な-api-を追加する手順) に沿って
   contract / Route / 証明書 / DNS / access Application を追加します。
5. [静的検証](reference.md) を実行し、Git 反映後に Application と実際のサービス操作を確認します。

イメージはベース YAML だけでなく、Kustomize の `images` で上書きされる場合があります。
更新対象は `kustomize build manifests/apps/<app>` の最終出力で確認します。

## sandbox 配布

[Runner 生成スクリプト](../automation/scripts/github-actions/add-runner.sh) は `STRATEGY=latest` のみを受け付けます。
GitHub-hosted runner で build → artifact → 共通 ARC runner `harbor-publish-runners` で `crane push` → Harbor の `latest` を更新します。
Organization `home-lab-ksera` の単一 ScaleSet（min=0/max=2）に集約し、公開処理は全 repo 合計で最大2並列、listener は1つです。
全7repoの公開workflowは `build-<run-id>-<attempt>-<commit-sha>` タグを付け、
default branch HEADとの一致を確認した版だけ `latest` へ昇格します。push jobはrepo単位で直列化します。
新しいrepoには生成workflowをそのrepoへ反映してください。
PR イベントでは build までです。ARC runner に Docker daemon、sudo、kubectl は不要です。

[Image Updater](../manifests/platform/ci-cd/image-updater/sandbox-images.yaml) が約2分間隔で
許可した8アプリの `deploy` の digest を検知し、このリポジトリの `main` にある各
`manifests/apps/<app>/kustomization.yaml` に書き戻します。ArgoCD Application の revision は `HEAD` のままです。
Harbor への接続は内部 URL、マニフェスト内のイメージ名は従来の `harbor.qroksera.com/sandbox/...` を使います。
5分間隔のHarbor保守Jobが直前の配布成功を確認して`latest`を`deploy`へ進めます。
更新方式は公式の [Git write-back](https://argocd-image-updater.readthedocs.io/en/stable/basics/update-methods/) と
[digest strategy](https://argocd-image-updater.readthedocs.io/en/stable/basics/update-strategies/#digest---update-to-the-most-recent-pushed-version-of-a-tag) を利用します。

- Deployment: Podテンプレートのdigestを更新します。api-hubはAPI/配送の1 replica/Recreateで短い受付停止があり、他のworkloadは各更新方式に従います。
- StatefulSet: cameraのrecorder/uploaderを同じdigestで更新し、旧Pod終了後に交代します。PVC・manifest・固定object keyは維持します。
- CronJob: Job テンプレートを更新し、次回作成の Job から新イメージになります。実行中の Job は継続します。
  実行中 Pod がない場合も検知できるよう `forceUpdate: true` を指定しています。
- 同じ Application 内の同じイメージを使う Deployment / CronJob は Kustomize の `images` で一緒に更新されます。
  別 Application 間の更新・同期は独立しており、同時切替は保証しません。

Git に digest が残るため、履歴から配布イメージを特定できます。
ロールバックはHarborの`maintenance.json`に既知の正常版を`rollbackDigest`として設定し、Gitへ反映します。
保守Jobが`deploy`を戻し、Image UpdaterとArgoCDが配布します。解除するまで指定版を維持します。
保持契約と保守手順は [Harborの3世代保持](harbor-retention.md) を参照してください。

## 既存 CI からの移行

このリポジトリの変更だけでは、外部のアプリリポジトリの workflow は更新されません。
旧 workflow の `docker load/push`、`kubectl get secret`、`rollout restart` は新 Runner で実行できません。
次の順で切り替えます。

1. Harbor に `sandbox` project の pull / push 専用 Robot Account を作成し、CI に管理者権限を渡さないようにします。
   Image Updater には別の pull-only Robot Account を作成します。
2. このリポジトリ専用の書き込み可能 Deploy Key を作成します。
   `main` への bot の書き込みをリポジトリの保護ルールで許可する必要があります。
   PR 必須ルールで直接 push が拒否される場合、この自動配布方式は開始できません。
3. Pulumi ESC の既存ストアに下表のキーを登録します。平文を Git に保存しません。
4. アプリ CI の push を一時停止し、実行中ジョブの終了を待ちます。
   `make add-runners-all` または個別の生成コマンドで作成した workflow を各アプリリポジトリへ反映します。
   独自の build / テストがある場合は維持し、push job を置き換えます。
5. このリポジトリを反映し、ExternalSecret の Ready、旧 Role / RoleBinding の prune、Runner のテンプレート変更、
   `image-updater` の同期を確認します。既存 Pod のトークンは変更で消えないため、旧 Runner Pod が残っていないことも確認します。
6. CI を再開し、1アプリの push → digest の Git commit → ArgoCD 同期 → imageID を確認します。
   CronJob は次回 Job の image / imageID を確認します。

| ESC キー | 用途 |
|---|---|
| `harbor.ci_username` / `harbor.ci_password` | Runner の sandbox pull / push Robot Account |
| `harbor.image_updater_username` / `harbor.image_updater_password` | Image Updater の sandbox pull-only Robot Account |
| `image_updater.git_ssh_private_key` | このリポジトリへの Git 書き込み用 Deploy Key の秘密鍵 |

資格情報が未登録なら ExternalSecret は Ready になりません。admin 認証へのフォールバックはしません。
新 Runner は専用の `harbor-ci-auth` をマウントします。移行前の admin 入り `harbor-auth` が残っていても利用しません。
旧 Secret の参照がなくなったことを確認し、残存していれば移行後に削除します。
共通 sandbox Robot Account には同 project 内のイメージを push できる権限が残ります。
アプリ間のレジストリ権限も厳密に分離する場合は、Harbor project と Robot Account をアプリ別に分離します。
Git 資格情報もファイル単位の権限制限ではないため、Runner には渡しません。
専用 Deploy Key は ArgoCD の `apps` project 用 repository credential として登録し、Image Updater が利用します。
`method: git` と `insecure: false` により、ArgoCD の既知ホスト情報で SSH host key を検証します。
この版の `git:secret:...` は SSH host key 検証を無効にするため使用しません。

確認例（Secret の値を表示しない）:

```bash
kubectl -n arc-systems get externalsecret harbor-auth-secret
kubectl -n argocd get externalsecret image-updater-git image-updater-harbor
kubectl auth can-i patch deployments -n sandbox --as=system:serviceaccount:arc-systems:github-actions-runner
kubectl auth can-i get secrets -n arc-systems --as=system:serviceaccount:arc-systems:github-actions-runner
kubectl -n arc-systems get autoscalingrunnersets,pods
kubectl -n argocd get imageupdater sandbox-images -o yaml
kubectl -n argocd logs -l app.kubernetes.io/name=argocd-image-updater --tail=100
```

権限確認は両方 `no` が期待値です。新 Runner Pod には特権コンテナ、Docker socket、API トークンのマウントがないことも確認します。

## 共通 Runner を使う repo を追加する

PyYAML を含む toolchain で実行します。登録先と Runner 名は共通の GitOps 定義から読み取ります。

```bash
mise exec -- bash
make add-runner REPO=my-app
# settings.toml の arc_repositories から一括生成する場合
make add-runners-all
```

生成処理は workflow のみを出力し、[runners-appset.yaml](../manifests/platform/ci-cd/github-actions/runners-appset.yaml) は変更しません。
新しい repo は `home-lab-ksera` に作成し、Organization の runner group `harbor-publish` の Selected repositories に追加します。
公開 repo を含むため同 group の public repository 利用を許可し、公開ジョブの PR 除外条件を維持します。
同時実行数を変える場合は共通 ScaleSet の `maxRunners` を Git で変更します。旧 CLI/settings の repo 別 min/max は使用しません。

ARC の PAT は Resource owner を `home-lab-ksera` とし、Organization permissions の `Self-hosted runners: Read and write` と
公式 ARC 認証手順の `Administration: Read` を設定します。Pulumi ESC の `github` キーから ExternalSecret で同期します。
**Make 経由の workflow 出力先は `automation/scripts/github-actions/.github/workflows/build-and-push-<repo>.yml`** です。
Make がスクリプトのディレクトリへ移動し、スクリプトが相対パスで出力するためです。
生成物は Git 非追跡なので、内容を確認してアプリリポジトリの `.github/workflows/` に配置します。

生成後は、アーティファクトの名前とファイル形式（gzip 圧縮した `docker save`）、Runner 名、Harbor の push 先を確認します。
イメージ名はリポジトリ名を小文字化します。`crane` はバージョンと SHA256 を固定し、一般ユーザーで取得・実行します。
Docker コンテナ action、job container、services、sudo を必要とする処理は GitHub-hosted runner 側へ置きます。

新規アプリを自動更新するには [sandbox-images.yaml](../manifests/platform/ci-cd/image-updater/sandbox-images.yaml) に
Application 名とイメージを登録し、対象 Kustomization に `images` エントリを追加します。
Runner 追加だけでは自動配布対象にしません。

[Runner ServiceAccount](../manifests/platform/ci-cd/github-actions/github-actions-rbac.yaml) に API 権限はありません。
Harbor 認証は Secret volume として読み取り専用で渡し、ServiceAccount トークンはマウントしません。
controller の手動 Helm 導入は不要です。
