# NetworkPolicy

Flannel は NetworkPolicy を実行しないため、[kube-network-policies](../manifests/infrastructure/networking/kube-network-policies/manifest.yaml)
の DaemonSet が全ノードで実行します。

## どこを絞っているか

| Namespace | 方針 |
|---|---|
| `sandbox`、`tools`、`harbor` | 受信・送信とも default-deny。必要な通信だけを許可 |
| `database`、`rustfs` | 受信だけを絞る（PostgreSQL、RustFS への接続元を限定） |
| `observability` | Loki への受信だけを絞る |
| `argocd` | upstream が同梱するポリシー |
| それ以外（`cert-manager`、`external-secrets-system`、`nginx-gateway`、`tailscale` など） | 制限なし |

基盤の namespace を絞っていないのは、入口が LAN と Tailscale に限られていて、保守の手間に見合わないためです。

## 定義の場所

| ファイル | 内容 |
|---|---|
| [core/workload-networkpolicies.yaml](../manifests/core/workload-networkpolicies.yaml) | `sandbox`・`tools`・`harbor` の default-deny と、アプリ・Harbor の許可 |
| [core/application-networkpolicies.yaml](../manifests/core/application-networkpolicies.yaml) | Gateway → media API、VictoriaMetrics → アプリの metrics |
| [core/networkpolicies.yaml](../manifests/core/networkpolicies.yaml) | RustFS への受信（Gateway、api-hub） |
| [shared-postgresql/postgresql.yaml](../manifests/platform/shared-postgresql/postgresql.yaml)、[application-networkpolicy.yaml](../manifests/platform/shared-postgresql/application-networkpolicy.yaml) | PostgreSQL への受信と、sandbox からの送信 |
| [rustfs/lifecycle-networkpolicy.yaml](../manifests/platform/rustfs/lifecycle-networkpolicy.yaml) | lifecycle Job → RustFS |
| [observability/logging-networkpolicy.yaml](../manifests/platform/observability/logging-networkpolicy.yaml) | Alloy・Grafana → Loki |
| [apps/nature-remo/access.yaml](../manifests/apps/nature-remo/access.yaml) | nature-remo の受信と、Remo 本体への送信 |

## sandbox で許可している通信

| 送信元 | 宛先 |
|---|---|
| すべての Pod | CoreDNS（53） |
| 共有 Gateway | blog:80、cooklog:80、hitomi-upload-viewer:80、media-api:3000、api-hub:3000、rsshub:1200、nature-remo:8080 |
| home-camera-recorder、hitomi-monitor、hitomi-outbox、hitomi-pdf-monitor、market-collector、media-api | api-hub:3000 |
| api-hub | RustFS:9000 |
| `database.k8s-myhome.local/shared-postgresql: "true"` のラベルを持つ Pod | PostgreSQL:5432 |
| rsshub | rsshub-redis:6379 |
| hitomi-monitor、hitomi-pdf-monitor | 共有 Gateway:443（内部の RSSHub URL を使うため） |
| api-hub、hitomi-monitor、hitomi-pdf-monitor、rsshub | 外部の 80 / 443 |
| cooklog-slack、market-collector | 外部の 443 |
| home-camera-recorder | カメラ `192.168.10.4` の RTSP（TCP 554、UDP 1024-65535。UDP は受信も許可） |
| nature-remo | Remo 本体 `192.168.10.2:80` |
| VictoriaMetrics | api-hub:3000、media-api:3000、hitomi-outbox:9090、home-camera-recorder:9090（metrics） |

`harbor` では、Harbor の Pod 同士、Gateway → core / portal（8080）、Harbor → PostgreSQL（5432）を許可しています。

「外部」は `0.0.0.0/0` から private・loopback・link-local・CGNAT の範囲を除いたものです。IPv6 の許可はありません。

## 知っておくこと

- **宛先のドメインは絞れません。** 外部 443 を許可した Pod は、どの HTTPS サイトにも接続できます。
- **Gateway を許可すると、同じ Gateway の他の URL にも届きます。** hitomi からは RSSHub 以外の内部 URL にも到達できます。
- **許可は足し算です。** 狭いポリシーを足しても、別の広い許可は消えません。
- **Service の port と Pod の port は別です。** Service が 80 → 8080 なら、HTTPRoute は 80、NetworkPolicy は 8080 を書きます。
- **Gateway 経由の通信の送信元は NGINX の Pod です。** 利用者の IP や HTTP のパスでは絞れません。
- **イメージの pull はノードの通信です。** Pod の Egress の許可とは関係ありません。
- **同じノードのホストから Pod への通信は評価されません。** kubelet の probe や API サーバーからの通信が該当します。
  別ノードのホストからの通信は、送信元がそのノードの `flannel.1` のアドレス（control-plane なら `10.244.0.0`）になります。
- **Gateway の VIP は IP で書いています。** `192.168.122.100` を変えるときは、ポリシーも直します。
- controller は `--fail-open=true` で動かしています。controller が止まると通信は遮断されず、すべて通ります。
  停止は `NetworkPolicyControllerUnavailable` が通知します。

## 許可を足す

1. 送信元と宛先の namespace・Pod のラベル、Pod の待受 port を決める。
2. `sandbox` は送信も default-deny なので、Pod 同士の通信は **送信側の Egress と受信側の Ingress の両方** を書く。
3. 同じ `from` / `to` の要素に `namespaceSelector` と `podSelector` を並べると「両方を満たす」、別の要素に書くと「どちらかを満たす」になる。
4. DB を使う Pod には `database.k8s-myhome.local/shared-postgresql: "true"` のラベルを付ける（ポリシーの追加は不要）。
5. metrics を収集させるなら、[application-networkpolicies.yaml](../manifests/core/application-networkpolicies.yaml) に VictoriaMetrics からの受信を足す。

`automation/tests/setup/test_workload_networkpolicies.py` が許可の組み合わせを検証しています。許可を変えたらテストも直します。

## 確認

```bash
kubectl -n kube-system get ds kube-network-policies
kubectl get networkpolicy -A
kubectl -n kube-system logs daemonset/kube-network-policies --tail=100
```

拒否の確認は、Pod から Pod へ実際に接続して行います。port-forward やノードからの接続では確認になりません。
HTTP 403 が返るのは、通信がアプリまで届いている状態です。
検証用の Pod に本番の Service と同じラベルを付けると、Service の配信先に入ってしまうので避けます。

切り戻しは Git の revert です。ポリシーの誤りで ArgoCD 自体が同期できなくなった場合は、
該当の NetworkPolicy を `kubectl delete` で外してから Git を直します（selfHeal で戻るため、先に Git を直せるならそうします）。
