# NetworkPolicy の運用

[Flannel](../automation/infrastructure/main.tf) に加え、
[kube-network-policies](../manifests/infrastructure/networking/kube-network-policies/manifest.yaml) が Linux ノード上でポリシーを実行します。
宣言済みの controller は全 Linux ノードが対象で、初回カナリア導入途中の構成ではありません。

## ポリシーの所有者

| 正本 | 選択する対象と許可 |
|---|---|
| [core/networkpolicies.yaml](../manifests/core/networkpolicies.yaml) | Gateway → RustFS:9001、sandbox の `app=api-hub` → RustFS:9000 |
| [RustFS lifecycle](../manifests/platform/rustfs/lifecycle-networkpolicy.yaml) | 同 namespace の lifecycle Job → RustFS:9000 |
| [PostgreSQL](../manifests/platform/shared-postgresql/credentials.yaml) | database 内 Pod、harbor の `app=harbor`、observability の Grafana → DB（namespace 外は TCP 5432） |
| [Loki](../manifests/platform/observability/logging-networkpolicy.yaml) | Grafana / Alloy → Loki:3100 |
| [core/platform-networkpolicies.yaml](../manifests/core/platform-networkpolicies.yaml) | 基盤 5 namespace の default-deny と許可（下の「基盤 namespace」） |

[workload-networkpolicies.yaml](../manifests/core/workload-networkpolicies.yaml) は
`sandbox`・`tools`・`harbor` に Ingress/Egress 両方の default-deny を設定します。

| 接続元 | 許可する宛先 |
|---|---|
| 各対象 namespace | kube-system の CoreDNS TCP/UDP 53 |
| NGINX Gateway | sandbox の公開アプリ（80 / 3000 / 1200）、Harbor core / portal:8080 |
| home-camera / hitomi-pdf | api-hub:3000（hitomi-pdf も内部 Service を使用） |
| api-hub | RustFS:9000、外部 HTTP/HTTPS（Slack 等） |
| hitomi-monitor（Selenium sidecar を含む） | 外部 HTTP/HTTPS。WebDriver は同一 Pod の loopback:4444 |
| hitomi-pdf / rsshub | 外部 HTTP/HTTPS |
| hitomi-pdf | Gateway:443（内部 RSSHub URL） |
| rsshub | rsshub-redis:6379 |
| home-camera | 192.168.10.4:554/TCP と RTSP の RTP/RTCP 用 UDP 1024–65535（同カメラからの受信も許可） |
| Harbor 内部（cleanup Job 含む） | 同 namespace の Harbor Pod の TCP 8080 / 5000 / 8082 / 6379 |
| Harbor | shared-postgresql:5432 |

## 基盤 namespace

[platform-networkpolicies.yaml](../manifests/core/platform-networkpolicies.yaml) は
`observability`・`cert-manager`・`external-secrets-system`・`nginx-gateway`・`tailscale` に
Ingress/Egress 両方の default-deny と DNS の許可を置きます（2026-11-15）。`argocd` は upstream のポリシー、
`database` と `rustfs` は各コンポーネントのポリシーで守られています。

| 対象 | 受信 | 送信 |
|---|---|---|
| VictoriaMetrics | Alloy / Grafana / vmalert / 週次レポート、Harbor 保守、RustFS scanner、Gateway（VMUI）→ 8428 | API、全 Pod の metrics 用 port（3000 / 8080 / 9090 / 9113 / 9402）、ノードの 10250 / 9080、ホストの 9100 |
| vmalert / Alertmanager | Alertmanager は vmalert → 9093 のみ | vmalert → VM / Alertmanager。Alertmanager → 外部 HTTPS（Slack、healthchecks.io） |
| Grafana | Gateway → 3000 | VM、Loki、共有 PostgreSQL のみ。外部へは出ない |
| Alloy | なし | API、Loki、VM、外形監視（Gateway 443、Split DNS 53） |
| kube-state-metrics / 週次レポート | VM → 8080 / なし | API / VM と外部 HTTPS（Slack） |
| cert-manager | webhook は API サーバー → 10250、全 Pod は VM → 9402 | 全 Pod → API。controller → 外部 HTTPS（ACME、Cloudflare API、DNS over HTTPS） |
| External Secrets | なし | API、外部 HTTPS（Pulumi ESC） |
| Gateway data plane | 全送信元 → 80 / 443、VM → 9113 | control plane 8443、HTTPRoute の backend の namespace と port のみ |
| Gateway control plane | data plane → 8443、VM → 9113 | API のみ（製品テレメトリは values で停止） |
| Gateway の cert-generator（chart の hook Job） | なし | API のみ。同期のたびに走るため、ないと同期が失敗する |
| Tailscale operator | なし | API、外部 HTTPS |
| Tailscale proxy | なし | API、外部 TCP 80 / 443、UDP の任意 port（LAN の端末と直接つながるため 192.168.0.0/16 を含む） |
| subnet router | なし | 192.168.122.0/24 の全 port、Gateway と Split DNS の Pod |
| Nature Remo の Ingress proxy | なし | sandbox の nature-remo:8080 のみ |
| Split DNS | 全送信元 → 53 | なし（hosts の固定応答だけで上流へ問い合わせない） |

VictoriaMetrics の収集先は Pod の `prometheus.io/scrape` annotation で決まるため、送信側は metrics 用の port に限って
全 Pod を許可し、受け側の ingress で絞ります。新しい収集先の port がこの一覧にない場合は追加します。
cert-manager の DNS01 の伝播確認は DNS over HTTPS（1.1.1.1 / 1.0.0.1）に固定しているため、外部の 53 番は開けていません。

### kube-network-policies の実測した性質（2026-11-15）

- 同じノードの root プロセス（kubelet の probe、API サーバー、containerd）から Pod への通信は評価されない。
- 別ノードのホストから Pod への通信は、送信元が送信側ノードの flannel.1 のアドレスになる。
  API サーバー（control-plane）から別ノードの webhook へは `10.244.0.0` から届く。
  control-plane は最初に参加するノードのため `10.244.0.0/24` を受け持つ。再構築で変わった場合は
  cert-manager webhook の許可も直す。
- 確認方法: 別ノードに置いた検証 Pod へ、送信元を 1 つだけ許可したポリシーを付け、ホストから接続する。

外部 HTTP/HTTPS は IPv4 の private / loopback / link-local / CGNAT 宛を除外しています。
標準 NetworkPolicy は FQDN や HTTP の宛先を制限できないため、許可された HTTPS を使った情報持ち出しは防げません。
宛先ドメインの制限には別途 egress proxy 等が必要です。IPv6 の外部許可はありません。
Gateway の許可は共有 VIP を使うため、CI と hitomi-pdf から同じ Gateway 上の他の HTTPS route にも到達可能です。
API Service と Gateway VIP は DNAT の前後の違いを考慮して明記しており、IP 変更時は本ポリシーも更新します。
イメージ pull は kubelet によるノード通信で、Pod の Egress 許可とは別です。
RustFS は api-hub と lifecycle Job の両方に S3 の許可があり、「api-hub 以外は一切接続できない」という説明は不正確です。

## controller の設定と限界

- `--disable-nri=true`：Pod 情報は API informer から取得します。
- `--strict-mode=true`：既存接続への影響も含めて更新時に確認します。
- `--fail-open=true`：controller / queue 障害時の可用性を優先します。常時遮断を保証する境界ではありません。
- nftables / NFQUEUE とホストネットワークを使います。readiness の成功は通信遮断の実測を代替しません。

実際の引数・権限・マウントは上記 DaemonSet が正本です。controller の利用不能と metrics 欠落は
[alert-rules.yaml](../manifests/platform/observability/config/alert-rules.yaml) に監視があります。

## 許可を追加するとき

[アクセス追加](access.md#新しく到達可能な-api-を追加する手順) と合わせて、宛先 namespace / Pod selector、
接続元 namespace / Pod selector、Pod の待受 port を指定します。
同じ peer に namespaceSelector と podSelector を書くと両方を満たす対象、別の peer に書くとどちらかを満たす対象です。

既存の許可は合算されるため、狭いポリシーを足しても別の広い許可は取り消せません。
Gateway 経由の backend から見た接続元は NGINX Pod です。NetworkPolicy で HTTP パスや利用者の認可を表現しません。

## 検証と切り戻し

```bash
kubectl -n kube-system get ds kube-network-policies
kubectl get networkpolicy -A
kubectl -n kube-system logs daemonset/kube-network-policies --tail=100
```

導入前に home-camera の新しい Pod ラベルと hitomi-pdf の接続先変更を同期し、古い Job の完了を確認します。
core の同期で default-deny と許可ポリシーが順次作られるため、初回同期は短時間の通信断があり得ます。

許可された実アプリ経由の操作、無関係な Pod からの Service IP / Pod IP への接続拒否を複数ノードで確認します。
port-forward やノード発の通信だけでは Pod 間の拒否試験になりません。HTTP 403 はアプリへの到達を意味します。
検証 Pod に本番 Service と同じラベルを付けて、誤って Service の配信対象にしないでください。

通常の切り戻しはポリシー変更の Git revert と同期です。
GitOps 自体が通信障害で止まった場合は、影響する namespace と port に限定した暫定許可を検討し、
Git 修正の反映後に撤去します。controller 全停止は全ポリシーへ影響するため、局所修正と分けて判断します。

最低限の疎通試験:

- Gateway から api-hub / blog / cooklog / viewer / RSSHub と Harbor にアクセスできる。
- hitomi-pdf から `https://rsshub.internal.qroksera.com/twivideo/ranking` を取得できる。
- api-hub の S3 操作、RSSHub のキャッシュ、hitomi の Selenium 操作、カメラ収録が成功する。
- Harbor の DB 接続が成功する。
- VictoriaMetrics の全収集対象が `up == 1`、外形監視が全件 `probe_success == 1`、Loki にログが入り続ける。
- Alertmanager の Slack / heartbeat 送信が失敗しない（`alertmanager_notifications_failed_total` が増えない）。
- cert-manager の webhook が応答し（`kubectl apply --dry-run=server` で Certificate を検証）、証明書を発行できる。
- ExternalSecret の `force-sync` 後に Ready のまま更新時刻が進む。
- subnet router から Gateway VIP と Split DNS に届き、Tailnet の端末との直接通信が維持される。
- 無関係な Pod から Grafana / VictoriaMetrics / Alertmanager / cert-manager webhook への接続が失敗する。
- 無関係な sandbox Pod から hitomi Pod の WebDriver:4444、Redis、API Server、LAN への通信が失敗する。
- 許可されていない Pod の外向き TCP 443 と、許可された Pod の外向き TCP 22 が失敗する。
- api-hub Service が ClusterIP であり、ノードの旧 32001/TCP から到達できない。

これらの実測は宣言の静的検証では代替できません。ノード自身や hostNetwork Pod に対する標準 NetworkPolicy の制約も考慮します。
公式の [NetworkPolicy の動作](https://kubernetes.io/docs/concepts/services-networking/network-policies/) も参照してください。

Selenium の Service と tools 向け WebDriver 許可は廃止しました。同一 Pod の loopback 通信に
NetworkPolicy の追加許可は不要です。ブラウザも hitomi Pod の外向き通信許可を共有します。
