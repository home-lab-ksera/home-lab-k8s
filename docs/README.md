# 運用ドキュメント

この文書群は **2026-10-20 にリポジトリの実装を確認して再構成した手順書**です。
記載する構成は Git の宣言であり、稼働クラスタの実測結果ではありません。
コマンドは特記がなければリポジトリのルートで実行します。`<name>` などは実際の名前に置き換えてください。

## 目的から探す

| 目的 | 文書 |
|---|---|
| 構成と管理境界を知る | [全体構成](architecture.md)、[配置規約](manifests.md)、[ホスト容量と VM サイズ](host-capacity.md) |
| ホスト・クラスタを構築する | [初期構築](bootstrap.md) |
| Git の変更を反映する | [GitOps](gitops.md)、[Application と Sync Wave](diagrams/app-of-apps-sync-wave.md) |
| アプリ・公開 workflow を追加する | [アプリ配布](app-delivery.md)、[RSSHub](rsshub.md) |
| URL・DNS・通信許可を変更する | [アクセス経路](access.md)、[NetworkPolicy](network-policy.md) |
| 認証情報を用意・更新する | [Secret 管理](secrets.md) |
| 状態・ログ・アラートを調べる | [日常運用](operations.md)、[監視](observability.md)、[障害対応](troubleshooting.md) |
| DB を利用・バックアップ・復旧する | [共通 PostgreSQL](shared-postgresql.md) |
| バージョンを更新する | [ノード更新](upgrade.md)、[アプリ更新](application-auto-updates.md)、[PostgreSQL 更新](postgresql-updates.md) |
| Reloader や補助基盤を確認する | [プラットフォーム拡張](platform-extensions.md) |
| 検証コマンドを調べる | [検証・参照](reference.md) |

## 正本と確認範囲

- 実行入口は [Makefile](../Makefile)、フェーズの順序は [run.sh](../automation/scripts/run.sh) です。
- Kubernetes の宣言は [manifests/](../manifests/)。Application の参照先と Kustomize の登録を辿って管理対象を判断します。
- ローカル設定は [settings.toml.example](../automation/settings.toml.example) を元に作成します。設定ファイルだけで全マニフェストが生成される構造ではありません。
- 将来計画・意思決定は [tasks/](../tasks/)、過去の更新記録は [archive/](archive/README.md) に分けます。
- CLI の終了成功、静的検証、実クラスタの正常性、バックアップの復元成功は別々に確認します。

構成変更時は関連ページと根拠ファイルへのリンクを更新してください。Application の追加・削除・wave 変更では
[一覧と図](diagrams/app-of-apps-sync-wave.md) も更新します。版番号の正本は manifest / chart / lock file とし、手順に不要な版番号は複製しません。
