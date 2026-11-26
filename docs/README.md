# ドキュメント

ホームラボの Kubernetes クラスタの、現在の構成と運用手順です。
過去の経緯や移行手順は載せません。必要なときは git の履歴と PR を参照します。

## 目的から探す

| 知りたいこと | 文書 |
|---|---|
| 何がどこで動いているか | [全体構成](architecture.md) |
| ホストと VM の設定、容量、証明書 | [ホストとノード](nodes.md) |
| クラスタを最初から作る、作り直す | [構築と再構築](bootstrap.md) |
| マニフェストの置き場所、Application 一覧、反映の流れ | [GitOps](gitops.md) |
| 変更を検証する、CI が何を見ているか | [検証と CI](validation.md) |
| URL、DNS、TLS、Tailnet | [アクセス経路](access.md) |
| Pod 間の通信許可 | [NetworkPolicy](network-policy.md) |
| Secret の出どころと更新 | [Secret 管理](secrets.md) |
| PVC、NFS、RustFS、Harbor のデータ | [ストレージ](storage.md) |
| DB の接続、バックアップ、復元 | [PostgreSQL](postgresql.md) |
| 各アプリの役割と動き方 | [アプリ一覧](apps.md) |
| アプリの追加、ビルドから配布まで | [アプリ配布](app-delivery.md) |
| メトリクス、ログ、アラート | [監視](observability.md) |
| 日々の確認、再起動後、ノード保守 | [日常運用](operations.md) |
| 症状から原因を探す | [障害対応](troubleshooting.md) |
| 版を上げる | [更新](updates.md) |

## 書き方の約束

- 設定値の正本はマニフェストとスクリプトです。文書には、読んだだけでは分からない理由・手順・注意点を書きます。
  版番号やリソース量など、ファイルを見れば分かる値は写しません。
- 現在の状態だけを書きます。撤去したものや移行の経緯は書きません。
- 1 つの事柄は 1 か所に書き、他の文書からはリンクします。
- Application の追加・削除や sync wave の変更をしたら、[GitOps の一覧](gitops.md#application-一覧) を同じ PR で直します。
- コマンドは、特記がなければリポジトリのルートで実行します。`<name>` は実際の名前に置き換えます。

作業の計画と進捗は [tasks/](../tasks/) に置きます（進行中の計画がなければ空です）。
