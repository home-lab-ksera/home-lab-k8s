# Nature Remo の内部URLとTailscale公開

Local API を利用する家電リモコン Web アプリです。
Nature のクラウド障害時の代替操作が目的です。保存済み信号を LAN 経由で送信し、Cloud API への接続やトークンを必要としません。
アプリのソースは [home-lab-ksera/nature-remo](https://github.com/home-lab-ksera/nature-remo)（private）、GitOps の定義はこのリポジトリに置きます。
初回イメージは `build-975d107`、Kustomize で digest を固定しています。

## 利用URLと接続制限

通常の内部URLは **https://nature-remo.internal.qroksera.com** です。
CoreDNSとTailnet Split DNSで `192.168.122.100` に解決し、GatewayでTLSを終端します。
`/` が操作画面、`/healthz` が生存確認、`/api/state` が設定・保存済みボタンの読み取りAPIです。
Webアプリは同じoriginの相対URLでAPIへ接続するため、アプリのURL設定変更は不要です。

アプリ自身に利用者認証はありません。NGINX Gateway Fabric 2.8のAccessPolicyで、
LAN (`192.168.10.0/24`)、クラスタノード (`192.168.122.0/24`)、
Pod / subnet-router (`10.244.0.0/16`)、Tailnet (`100.64.0.0/10`) の送信元だけを許可します。
それ以外は403です。`internal`というDNS名自体がアクセス制御になるわけではありません。
許可ネットワーク内の端末・Podはアプリを操作できるため、これらを信頼する構成です。
既存のTailscale Ingress URLも維持し、そちらは引き続きTailnet ACL/grantsで制御します。

Gatewayの `externalTrafficPolicy: Local` と、HTTPヘッダーで送信元IPを上書きしない現行設定を前提とします。
外部プロキシやSNATを追加する場合は、Internetからの通信が許可ネットワークのIPに見えないか再確認してください。
AccessPolicyはRouteより先のsync-waveで適用します。CIは必須policyの削除、宛先変更、許可範囲変更を検出します。

内部DNSを利用する端末ではSplit DNS (`192.168.122.101`) に `internal.qroksera.com` を問い合わせます。
確認例（IPを直接指定してDNSを迂回するcurlではありません）:

```bash
dig @192.168.122.101 nature-remo.internal.qroksera.com A +short
curl --fail https://nature-remo.internal.qroksera.com/healthz
```

LAN端末でもこのDNSゾーンの転送設定が必要です。Tailscale接続端末では既存のSplit DNS設定を使います。

## 確認した実機

- 機種: Nature Remo mini 1（ユーザー申告）
- MAC: `50:02:91:f5:8c:35`
- IP: `192.168.10.2`（LAN の ARP 情報で MAC と一致を確認）
- HTTP 応答: `Server: Remo-mini/2.0.62-gf5b5d27`
- `GET /messages`: 最初は HTTP 404、リモコン操作後の受信確認で HTTP 200。
  `freq: 38` / `format: us` / `data`: 379 パルスの信号を取得済み。家電への送信は未検証。

上記 IP を接続先と NetworkPolicy に反映済みです。DHCP 予約の有無は未確認なので、
ルーターでこの MAC に `192.168.10.2` を予約してください。

## 初回設定

1. 使用機種が [Nature Remo Local API](https://local-swagger.nature.global/) に対応していることを確認し、
   ルーターで Remo の IP を固定します。Local API のない機種では使えません。
2. `manifests/apps/nature-remo/kustomization.yaml` の `REMO_DEVICES` を確認します。
   現在の設定:

   ```yaml
   - 'REMO_DEVICES=[{"id":"living","name":"Nature Remo mini","url":"http://192.168.10.2"}]'
   ```

3. `manifests/access/nature-remo/manifest.yaml` の `egress` は下記の設定済みです。
   複数台ある場合は各 IP の `ipBlock` を列挙します。

   ```yaml
   egress:
     - to:
         - ipBlock:
             cidr: 192.168.10.2/32
       ports:
         - protocol: TCP
           port: 80
   ```

   Pod のネットワークから Remo の LAN への経路も必要です。
   Tailscale Ingress はブラウザーからアプリへの経路を提供するもので、Remo 側への LAN 経路を自動で追加しません。

4. アプリのイメージをビルドし、既存の認証方法で Harbor にログインして push します。

   ```bash
   cd ../nature-remo
   VERSION=build-$(git rev-parse --short HEAD)
   docker build -t harbor.internal.qroksera.com/sandbox/nature-remo:$VERSION .
   docker push harbor.internal.qroksera.com/sandbox/nature-remo:$VERSION
   ```

   push で表示された digest を `manifests/apps/nature-remo/kustomization.yaml` の `images` に
   `digest: sha256:...` として追加してください。
   本アプリも Harbor 保守と Image Updater の対象です。初回正常稼働後、保守 Job が `deploy` と
   `retain-current` を作成し、以降は `deploy` の digest を Git に書き戻します。
   更新時はテスト済みイメージを `latest` にも push すると、保守 Job の確認後に配布されます。
   アプリの GitHub Actions はテストとビルドのみで、Harbor への自動 push は行いません。

5. Tailscale Operator が稼働し、MagicDNS と HTTPS が有効であることを確認します。
   Tailnet の `tagOwners` に `"tag:k8s": ["tag:k8s-operator"]` が必要です。
   2026-11-05 に既存ポリシーの他の設定を保持して追加しました。
   [Tailscale の公式 Ingress 説明](https://tailscale.com/docs/kubernetes-operator/ingress)を参照してください。
   利用者の ACL/grants を `nature-remo` の TCP 443 に許可します。
   アプリにログイン画面はなく、許可した利用者は信号の登録・削除・送信ができます。
6. 変更を通常の GitOps フローで反映します。bootstrap の Kustomization には
   `nature-remo` と `nature-remo-access` を登録済みです。
   イメージ未登録の段階ではこの変更を本番ブランチへ反映しないでください。

## 確認

```bash
kubectl -n sandbox get deployment nature-remo
kubectl -n sandbox get pvc nature-remo-data
kubectl -n sandbox get ingress nature-remo
kubectl -n sandbox logs deployment/nature-remo
```

Ingress の ADDRESS に表示される `nature-remo.<tailnet>.ts.net` を使って、
Tailscale に接続した端末から `https://nature-remo.<tailnet>.ts.net` を開きます。
初回の TLS 証明書発行には時間がかかる場合があります。実際の URL は Ingress の結果を確認してください。

HTTP の Service は ClusterIP のみです。NetworkPolicy は tailscale namespaceと、
nginx-gateway namespaceの共有Gateway Podから8080番への接続を許可します。
core の既定 deny と DNS 許可を前提とし、Remo への TCP 80 は上記の IP 個別指定で許可します。

ヘルスチェックは Web サーバーの生存を示します。実機の接続状態は登録画面の受信待ちで確認してください。
「リモコンを押してください」と表示されたら Remo に向けて押します。
Local API は受信日時を提供しないため、開始時の信号と比較して新しい信号を取得します。
完全に同じ信号は区別できないので、必要に応じて詳細欄の「直前の信号を取得する」を使います。

## 運用上の制約

- 保存先は NFS PVC の `buttons.json`。単一書き込みを維持するため `replicas: 1` / `Recreate` を変更しません。
- PVC は `Prune=false,Delete=false` で保護します。手動削除前に必ずバックアップしてください。
- 信号は取得・登録が必要です。Nature アプリの家電一覧や Cloud API の信号を自動取得する機能はありません。
- エアコンは設定を含む信号を登録します。家電の現在状態を推定して表示しません。
- 送信は自動再送しません。通信が失敗しても実機が操作済みの可能性があるため、家電の状態を確認します。
- `REMO_DEVICES` の変更は ConfigMap のハッシュ経由で再作成を引き起こします。既存ボタンを保つには `id` を維持します。
- Nature のクラウド障害に備える構成です。Kubernetes、PVC、家庭内 LAN、Remo、Tailscale 接続が稼働していることが前提です。
