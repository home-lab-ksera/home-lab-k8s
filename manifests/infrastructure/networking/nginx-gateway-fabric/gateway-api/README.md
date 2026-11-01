# Gateway API CRD

`standard-install-v1.6.3.yaml` は[公式リリース](https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.3/standard-install.yaml)の配布ファイルをそのまま同梱している。
ArgoCDのmanifest生成時にGitHubへの接続が遅いとタイムアウトするため、ローカルファイルを参照する。

更新時は公式配布ファイルを取得し、ファイル名と`kustomization.yaml`のバージョンを合わせて更新する。
