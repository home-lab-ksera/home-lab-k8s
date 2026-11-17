# 検証と CI

## 静的検証

CI とローカルで同じ [validate.sh](../automation/scripts/ci/validate.sh) を使います。

```bash
make validate          # Docker 内の専用 toolchain で実行（ホストに何も入れなくてよい）
make validate-local    # 必要な CLI を導入済みの環境で直接実行
```

`make validate` は [Dockerfile](../automation/toolchain/Dockerfile) からイメージを作り、手元と同じ UID で実行します。
イメージが作成済みなら、次でも実行できます。

```bash
docker run --rm -v "$PWD":/work -w /work k8s-myhome-validator
```

validate.sh が順に実行するもの:

| 段階 | 内容 |
|---|---|
| actionlint | `.github/workflows/` の構文 |
| 回帰テスト | [automation/tests/](../automation/tests/) の unittest（構築スクリプト、NetworkPolicy、週次レポート、CI スクリプト、DB 初期化）と、アラートルールの `promtool test rules` |
| shellcheck / yamllint | `automation/` の shell、`manifests/` と `automation/infrastructure/` の YAML |
| [gitops-check.py](../automation/scripts/ci/gitops-check.py) | Application の `HEAD`、source のパス、アプリと bootstrap Kustomization の登録漏れ |
| [policy-check.py](../automation/scripts/ci/policy-check.py) | 下の表 |
| kustomize + kubeconform | すべての `kustomization.yaml` を `--enable-helm` で描画して schema を検証 |
| [consistency-check.py](../automation/scripts/ci/consistency-check.py) | bootstrap の `targetRevision`、ExternalSecret の整合 |

policy-check の主な規則:

| 規則 | 落ちる条件 |
|---|---|
| R-004 | 自作イメージの `:latest` を sandbox 以外で使っている |
| R-005 | ExternalSecret を `platform/secrets/external-secrets/` の外に置いている |
| R-006 | bootstrap の外で `targetRevision: HEAD` を使っている。許可していない `prune: false` がある |
| R-007 | child Application の名前や source のパスが重複している |
| R-009 | 描画した結果、同じリソースを 2 つの Application が持っている |
| R-010 | `NodePort` / `LoadBalancer` / `externalIPs` の Service、または Ingress がある（許可リストは Split DNS だけ） |

静的検証で分からないこと:

- 外部 Helm chart の Application が実際に同期できるか、admission を通るか。
- Secret の実際の値、URL の疎通、データの復元。
- operator が後から作る Service や Pod（NGINX Gateway、Tailscale）。これらは描画結果に現れません。
- Markdown のリンク切れ。文書を動かしたら相対リンクを手で確認します。

## ローカルの toolchain

Docker を使わずに実行する場合は、[mise.toml](../mise.toml) が CLI の版を固定しています。

```bash
sudo apt-get install -y bash ca-certificates curl git jq libpq-dev postgresql python3-venv unzip xz-utils
mise trust && mise install
mise run setup       # Python の依存を .venv に入れる
mise run validate
```

一時的な PostgreSQL を起動するテストがあるため、一般ユーザーで実行します。

個別に確認する例:

```bash
shellcheck -S error -x automation/scripts/<file>.sh
yamllint -f parsable -c .yamllint.yml manifests/<dir-or-file>
kustomize build --enable-helm manifests/<component>
python3 automation/scripts/ci/gitops-check.py
python3 -m unittest discover -s automation/tests/setup -v
promtool test rules automation/tests/observability/*.test.yaml
```

## GitHub Actions

| workflow | 起動 | 内容 |
|---|---|---|
| [ci-deploy-validate.yml](../.github/workflows/ci-deploy-validate.yml) | push、PR、手動 | `validate`（validate.sh）、`setup-behavior`（構築スクリプトの回帰テスト）、`platform-configs`（Loki / Alloy / Renovate の設定を実際のイメージで検証） |
| [rebuild-drill.yml](../.github/workflows/rebuild-drill.yml) | 日曜 05:00 JST、手動、構築スクリプトを変える PR | 0 からの再構築訓練（[構築と再構築](bootstrap.md#再構築訓練)）。必須チェックではない |
| [renovate.yml](../.github/workflows/renovate.yml) | 毎日 09:00 JST、手動 | 更新 PR の作成（[更新](updates.md)） |
| [tailscale-policy.yml](../.github/workflows/tailscale-policy.yml) | `tailscale/` の変更、手動 | Tailnet の ACL を PR で検証し、`main` で適用（[アクセス経路](access.md#tailnet)） |
| [publish-harbor.yml](../.github/workflows/publish-harbor.yml) | アプリリポジトリから呼ぶ | イメージの build と Harbor への push（[アプリ配布](app-delivery.md)） |
| [rust-verify.yml](../.github/workflows/rust-verify.yml) | アプリリポジトリから呼ぶ | fmt、clippy、cargo-audit |

`main` は GitHub の ruleset で保護しています。変更は PR 経由で、`validate`・`platform-configs`・`setup-behavior` の成功が必須です
（承認は不要）。Image Updater の Deploy Key だけはこの制限を迂回して直接 push でき、
書き戻しの commit は `[skip ci]` 付きで検証を起動しません。
CI の job 名を変えるときは、ruleset の必須チェックも合わせて直します。そうしないと PR をマージできなくなります。

`platform-configs` を手元で実行する場合（Docker が必要）:

```bash
mise exec -- python3 automation/scripts/ci/validate-platform-configs.py
```

hitomi の Selenium sidecar は、一時的な kind クラスタで起動・終了を確かめるテストがあります（CI では実行しません）。

```bash
mise exec -- python3 automation/scripts/ci/selenium-runtime.py
```
