# アプリの追加と配布

アプリ実体は [apps/](../manifests/apps/)、公開経路は [access/](../manifests/access/) に置きます。
Application の namespace と source は [一覧](diagrams/app-of-apps-sync-wave.md) が参照先です。
現在のユーザーアプリは api-hub、blog、cooklog、hitomi、hitomi-pdf、hitomi-upload-viewer、home-camera、rsshub、nature-remo。
すべて `sandbox` に配置します。Selenium は独立アプリではなく、hitomi の収集 Job 内でのみ起動します。
起動待ち・終了・切替手順は [Selenium の非常駐化](hitomi-selenium-sidecar.md) を参照してください。
Nature Remo の初回配布と手動ビルドは [ローカル操作アプリの手順](nature-remo.md) を参照してください。

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

[workflow 生成スクリプト](../automation/scripts/github-actions/add-runner.sh) は `STRATEGY=latest` のみを受け付けます。
同じ GitHub-hosted runner で build → Tailscale OIDC 接続 → Harbor へ Docker push を行います。
Docker イメージの artifact 転送、クラスタ内 runner、Kubernetes Secret のマウントは不要です。
`build-<run-id>-<attempt>-<commit-sha>` タグを公開し、default branch HEAD と一致する版だけ `latest` へ昇格します。
両タグの image config digest がローカルのビルド結果と一致することも検証します。公開処理は repo / ref 単位で直列化します。
PR・手動実行は build までで、公開は生成時に取得した既定ブランチへの push に限定します。
アプリ固有のテストは Tailscale 接続より前に追加し、別 job のテストは build job の `needs` に指定してください。

[Image Updater](../manifests/platform/ci-cd/image-updater/sandbox-images.yaml) が約2分間隔で
許可した9アプリの `deploy` の digest を検知し、このリポジトリの `main` にある各
`manifests/apps/<app>/kustomization.yaml` に書き戻します。ArgoCD Application の revision は `HEAD` のままです。
書き戻しの commit message には `[skip ci]` を付け、digest だけの変更で検証 workflow を起動しません。
Harbor への接続は内部 URL、マニフェスト内のイメージ名は従来の `harbor.qroksera.com/sandbox/...` を使います。
10分間隔のHarbor保守Jobが直前の配布成功を確認して`latest`を`deploy`へ進めます。
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

生成した workflow を各アプリリポジトリへ反映します。独自のテスト、Dockerfile、複数イメージ構成は維持してください。
既存 workflow と重複して同じイメージを公開しないように統合します。

CI には Harbor の `sandbox` project の pull / push 専用 Robot Account を使います。
Image Updater の pull-only Robot Account と、このリポジトリへの書き込み用 Deploy Key は従来どおり別管理です。
`harbor.image_updater_username` / `harbor.image_updater_password` /
`image_updater.git_ssh_private_key` は Pulumi ESC → ExternalSecret 経由で同期します。
CI の資格情報は GitHub Secrets に登録します。値を Git に保存しません。

反映後は CI の push・digest 検証・latest 更新と、その後の Harbor 保守 Job による deploy 昇格、
Image Updater の Git 書き戻し、ArgoCD 同期、実際の imageID を確認します。
CronJob は次回 Job の image / imageID を確認します。

## 新規 repo の workflow と Secrets を準備する

作成済みの GitHub repo が対象です。必要なツールは Bash、Python 3、認証済みの GitHub CLI (`gh auth login`) です。
スクリプトが GitHub API から public / private と既定ブランチを取得します。main / master を推測しません。
API の読み取りと、private repo では Actions Secrets の書き込み権限が必要です。

```bash
make add-runner REPO=my-app
# 別の所有者を指定する場合
make add-runner REPO=owner/my-app
# settings.toml の arc_repositories から一括生成する場合
make add-runners-all
```

個別生成の既定所有者は `home-lab-ksera` です。`GITHUB_USERNAME` 環境変数で変更できます。
Make 経由の一括生成は `settings.toml` の `github.username` を読み込みます。
旧 min/max 引数と `arc_repositories` の min/max は互換用に受け付けますが使用しません。

- public: 既存の Organization Secrets を利用します。対象 repo が Secrets の公開範囲に含まれることを確認してください。
- private: 以下の4項目を環境変数から受け取り、未指定の項目は端末で非表示入力します。
  4項目が揃ってから `gh secret set` の標準入力で登録します。同名の repository Secrets は更新されます。

| Secret | 値 |
|---|---|
| `TS_OAUTH_CLIENT_ID` | Tailscale trust credential の Client ID |
| `TS_AUDIENCE` | 同 credential の Audience |
| `HARBOR_USERNAME` | Harbor Robot Account のユーザー名 |
| `HARBOR_PASSWORD` | Harbor Robot Account のパスワード |

端末以外から実行する場合は、4項目を環境変数で注入してください。
秘密値をコマンド引数や Make 変数に直接書かず、Secret Manager や非表示入力から export します。
一括生成も同じ環境変数を各 private repo に使用できます。
repo ごとに資格情報を分ける場合は個別に実行してください。

```bash
# 4項目が環境変数に設定済みの場合
make add-runner REPO=my-private-app RUNNER_ARGS=--non-interactive
# 既存の生成ファイルを意図して上書きする場合
make add-runner REPO=my-app RUNNER_ARGS=--force
```

非対話実行で必要な値が不足していれば、Secrets を書き込む前に失敗します。
途中の登録で API エラーになった場合は生成ファイルを更新せずに終了しますが、先に登録済みの Secrets は残ります。
原因を修正して同じ値で再実行してください。

**Make 経由の出力先は `automation/scripts/github-actions/.github/workflows/build-and-push-<repo>.yml`** です。
生成物は Git 非追跡です。内容を確認してアプリリポジトリの `.github/workflows/` に配置し、commit / push してください。
`WORKFLOW_DIR` 環境変数で出力ディレクトリを変更できます。既存ファイルは `--force` 指定時のみ上書きします。
イメージ名はリポジトリ名を小文字化し、ルートの Dockerfile からビルドします。

Tailscale 側では次の前提が必要です。

- GitHub issuer の trust credential が対象 Organization / repo と `push`、対象の既定ブランチを許可していること。
  現在の共有 credential は `ref: refs/heads/ma*` で main / master を許可しています。
  このパターンは maintenance など他の ma 始まりのブランチも許可します。
- credential に auth key 作成スコープと `tag:ci-harbor` が設定されていること。
- subnet route `192.168.122.0/24` が承認済みで、
  `tag:ci-harbor` → `192.168.122.100:443` が許可されていること。

生成 workflow は `harbor.internal.qroksera.com` を上記 IP に解決し、TLS 証明書を検証して接続します。
Tailscale Action は SHA を固定し、終了時に一時端末をログアウトします。Docker 認証ファイルも終了時に削除します。
runner group / ScaleSet の追加は不要です。

新規アプリを自動更新するには [sandbox-images.yaml](../manifests/platform/ci-cd/image-updater/sandbox-images.yaml) に
Application 名とイメージを登録し、対象 Kustomization に `images` エントリを追加します。
workflow 生成だけでは自動配布対象にしません。
