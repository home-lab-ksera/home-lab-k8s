# 個人用 Web UI の集約計画：dashboard

作成日: 2026-11-25

状態: 判断 1〜4 は決定済み。Phase 1 を実施中（下の「進行状況」）

## 進行状況

| ID | 状態 | 記録 |
|---|---|---|
| 0-1 | 完了（2026-11-25） | `home-lab-ksera/dashboard`（private） |
| 1-1 | 完了（2026-11-25） | 履歴 55 コミットを抜き出し、`c2182cb` で単一パッケージ化。lockfile の解決済みの版は元と同一 |
| 0-2 | 完了（2026-11-25） | repository Secret 4 つを登録、Tailscale の trust credential の対象を確認（管理者） |
| 1-2 | 完了（2026-11-25） | CI の test（typecheck・単体テスト・Playwright 3 本）と Harbor への publish が合格。`sandbox/dashboard@sha256:dcd4cae4…` |
| 1-3 | 確認待ち | api-hub#38（許可 Origin をリスト化）。RustFS の `cors.json` は 1-4 の PR に含む |
| 1-4 | 確認待ち | `manifests/apps/dashboard/`、Application、NetworkPolicy、Split DNS、外形監視、Image Updater、docs |
| 0-3 | 確認待ち | Harbor の保持ルールが `sandbox/dashboard` にもかかるかを Harbor の画面で確認する |

## 1. 目的

| 項目 | 成果 |
|---|---|
| viewer の独立 | `hitomi-pdf-batch/apps/upload-viewer` を履歴ごと新リポジトリ `dashboard` へ切り出し、hitomi-pdf-batch は Rust のバッチだけにする |
| 画面の集約 | メディア・カメラ・失敗管理（既存）、リモコン（nature-remo）、レシピ（cookLog）を `https://dashboard.internal.qroksera.com` の 1 つの SPA にまとめる |
| cookLog の再構築 | 下の要件を満たすよう、Eleventy の静的サイトから DB を持つ API サービスへ作り直す |

cookLog の要件（2026-11-25 に指定）:

1. Slack に URL を投稿するとレシピを取り込む
2. 手動でも取り込める
3. Web UI で閲覧できる
4. 名前と材料で検索できる

対象外: blog（読み物のサイトとして独立のまま）、Grafana・ArgoCD などの基盤 UI、Internet への公開。

## 2. 方針

- **BFF は作らない。** dashboard は静的ファイルだけを配る（今の busybox httpd のまま）。API は Gateway の HTTPRoute でパスごとに各バックエンドへ振り分ける。
  今の `/media/` → media-api と同じ形で、同一 Origin になるため CORS が要らず、nature-remo の同一 Origin 検査もそのまま通る。
- **バックエンドは各リポジトリに残す。** 画面だけを dashboard へ移す。
- **並行稼働してから切り替える。** 新ホスト名で動作を確認してから旧 Application と旧ホスト名を消す。旧 URL のリダイレクトは置かない（判断 3）。
- **クラスタ全体のリソースは先に定義だけを入れる。**（2026-11-14 の障害記録の教訓。DB・Secret・DNS も同様に、参照する側より先にマージして反映を確認する）

### パスの割り当て

| パス | 振り分け先 | 書き換え |
|---|---|---|
| `/media/` | media-api:3000 | なし（現状どおり） |
| `/remo/api/` | nature-remo:8080 | `ReplacePrefixMatch` で `/api/` へ |
| `/cook/api/` | cooklog:3000 | `ReplacePrefixMatch` で `/api/` へ |
| `/` | dashboard:80 | なし |

api-hub（署名付き URL の発行）は今と同じく `https://api-hub.internal.qroksera.com` を別 Origin で呼ぶ。

### メモリ

| 増減 | 対象 | requests |
|---|---|---|
| 増 | dashboard（viewer と同じイメージ構成） | ±0 |
| 増 | cooklog（Rust、HTTP API + Slack Socket Mode） | +32Mi 程度 |
| 減 | cooklog（nginx） | -32Mi |
| 減 | cooklog-slack（Node） | -128Mi |

全体ではおよそ 130Mi 減る見込み。nature-remo は画面を外すだけで変わらない。

## 3. 影響を受ける箇所（2026-11-25 時点）

旧ホスト名 `hitomi-upload-viewer.internal.qroksera.com` を参照している箇所:

| 場所 | 内容 | 対応 |
|---|---|---|
| `api-hub/src/server/guard.rs` | `S3_CORS_ALLOWED_ORIGIN` が定数 1 つ | 複数の Origin を許可できるようにし、移行中は新旧両方を許可。最後に旧を外す |
| `manifests/platform/rustfs/cors.json` | 署名付き URL の取得元 | 新旧両方 → 新だけ |
| `manifests/access/dns/core/coredns-configmap.yaml`、`dns/tailscale/manifest.yaml` | Split DNS の hosts | `dashboard` を追加し、各 Phase の切り替えで旧名を削除 |
| `manifests/core/workload-networkpolicies.yaml` | `allow-gateway-hitomi-upload-viewer` | dashboard 用を追加し、cooklog（新）への Gateway 経路も見直す |
| `manifests/platform/ci-cd/image-updater/sandbox-images.yaml` | 追跡するイメージ | `dashboard` を追加、`hitomi-upload-viewer`・`cooklog-slack` を削除 |
| 外形監視（docs/observability.md の対象 URL） | blackbox の probe 先 | `dashboard` を追加し、旧 URL を外す |
| `docs/apps.md`、`access.md`、`gitops.md`、`network-policy.md`、`architecture.md` | 一覧と経路 | 各 Phase の終わりに更新 |
| ワークスペース直下の `AGENTS.md` | リポジトリ一覧 | `dashboard` を追加し、hitomi-pdf-batch の説明から viewer を外す |
| hitomi-pdf-batch の CI・README・`docs/container-builds.md`・`scripts/*viewer*`・`test-camera.mjs` | viewer のビルドと検査 | dashboard へ移し、hitomi-pdf-batch から削除 |

## 4. Phase

### Phase 0 準備

| ID | 内容 | 担当 |
|---|---|---|
| 0-1 | GitHub に `home-lab-ksera/dashboard` を作る（公開範囲は判断 1） | 管理者 |
| 0-2 | `make add-runner REPO=dashboard` で publish workflow と Secrets を用意 | 管理者 |
| 0-3 | Harbor の `sandbox` project にイメージ `dashboard` の保持ルール（直近 5 件）を適用 | 管理者 |
| 0-4 | Split DNS に `dashboard.internal.qroksera.com` を追加（定義だけを先にマージ） | GitOps |

### Phase 1 viewer の切り出し（見た目と機能は変えない）

| ID | 内容 |
|---|---|
| 1-1 | `git filter-repo` で `apps/upload-viewer` と `scripts/test-viewer-api.mjs`・`test-camera.mjs`・`smoke-viewer.sh` の履歴を抜き出し、リポジトリの直下に置く。pnpm workspace をやめて単一パッケージにする |
| 1-2 | dashboard の CI: typecheck、`node --test`、build、smoke、Harbor への publish。Renovate を有効にする |
| 1-3 | api-hub の CORS を複数の Origin に対応させ、移行中だけ新旧両方を許可。RustFS の `cors.json` も同じ（1-6 で旧を外す） |
| 1-4 | `manifests/apps/dashboard/` と `user-apps/dashboard-app.yaml` を追加。HTTPRoute は `/media/` と `/` |
| 1-5 | 新 URL でメディア・動画・ランダム・カメラ・失敗管理の動作を確認 |
| 1-6 | `hitomi-upload-viewer` の Application と旧ホスト名（HTTPRoute、Split DNS、RustFS CORS、api-hub の許可 Origin）を削除 |
| 1-7 | hitomi-pdf-batch から viewer・pnpm・`publish-viewer` job を削除し、Rust 専用のリポジトリにする |

完了条件: dashboard ですべての画面が今と同じに動き、旧ホスト名への参照が残っていない。hitomi-pdf-batch に Node の資産が残っていない。

### Phase 2 共通の枠

| ID | 内容 |
|---|---|
| 2-1 | 上部のナビゲーション（メディア / カメラ / リモコン / レシピ）と、セクション単位のルーティングを入れる。busybox httpd に SPA fallback がないため、今と同じく各セクションの `index.html` をビルド時に複製する |
| 2-2 | `App.tsx`（303 行）からメディアの一覧を `sections/media/` に分け、セクションごとにディレクトリを持つ構成にする |
| 2-3 | 実行時の設定（`config.js`）をセクションごとに整理する |

### Phase 3 nature-remo の画面を移す

| ID | 内容 |
|---|---|
| 3-1 | dashboard に `/remo/api/` の HTTPRoute（URLRewrite）を追加し、既存の `/api/` がそのまま使えることを確認する。同一 Origin の検査と `X-Requested-With` が通ることを確認する |
| 3-2 | `nature-remo/web/app.js` の画面（Remo の選択、ボタンの送信、受信待ちと登録、削除）を React の `sections/remo/` に移す |
| 3-3 | 旧ホスト名 `nature-remo.internal.qroksera.com` の HTTPRoute と Split DNS を削除する |
| 3-4 | nature-remo から `web/` と `go:embed` を外し、API と `/healthz` だけのサーバーにする。README を更新する |

完了条件: Nature のクラウドなしで、dashboard から保存済みボタンの送信と新規登録ができる。

注意: nature-remo の目的は「Nature のクラウド障害時の代替操作」。dashboard が止まると操作できなくなるため、
dashboard は外部 CDN に依存しない（今の nature-remo と同じく、すべてのアセットをイメージに含める）。

### Phase 4 cookLog の再構築

cookLog リポジトリを Rust のサービスに作り直す。Eleventy・nginx のイメージ・Slack worker が GitHub へ push する仕組みはなくなる。

構成（1 つの Deployment、replica 1、api-hub と同じ形）:

| 実行モード | 内容 |
|---|---|
| `--serve` | HTTP API と Slack Socket Mode の受信を同じプロセスで動かす |
| `--migrate` | PreSync の Job で migration |
| `--import <url>` | 手動取り込みの CLI（`kubectl exec` か Job で実行） |
| `--import-markdown <dir>` | 既存の Markdown 記事（8 件）を一度だけ取り込む |

DB は shared-postgresql に `cooklog` を新設する。

| 表 | 主な列 |
|---|---|
| `recipes` | id、title、description、source_url（一意）、steps（順序付きの配列）、created_at、updated_at |
| `recipe_ingredients` | recipe_id、position、name、amount |

検索には `pg_trgm` を使う。title と `recipe_ingredients.name` に GIN 索引を張り、部分一致で名前と材料を検索する。
「ナス」と「なす」のように表記が揺れるものは、正規化した列（ひらがな → カタカナ、全角・半角の統一）でも照合する。

API:

| メソッドとパス | 内容 |
|---|---|
| `GET /api/recipes?q=&ingredient=` | 一覧と検索（名前と材料、複数の材料は AND） |
| `GET /api/recipes/{id}` | 詳細 |
| `POST /api/imports` | URL から取り込む（dashboard の手動取り込み） |
| `POST /api/recipes` / `PUT` / `DELETE` | 手で入力・修正・削除 |

取り込み処理: 共通の JSON-LD（`schema.org/Recipe`）の解析を先に試し、だめなら今のサイト別の処理（クラシル、DELISH KITCHEN、白ごはん.com）にする。
今の Node のテスト（`providers.test.js`、`worker.test.js`）は、同じ入力の fixture として Rust のテストへ移す。

Slack（判断 2 で決定）:

- チャンネルは今と同じ `C0CDULTS7FX`。
- 受信は Socket Mode で直接つなぐ（今の cooklog-slack と同じ例外）。
- 結果は api-hub の `POST /slack/message`（`thread_ts` 付き）で、元の投稿のスレッドに返す。
  成功なら dashboard の詳細へのリンク、失敗なら理由（対応外のサイト、取得失敗、取り込み済み）。
- 返信は再試行しない。送れなくてもレシピは DB と dashboard に残る。api-hub は改修しない。
- 投稿者の名前は api-hub の Bot になる（2026-11-25 にユーザーが了承）。api-hub の Bot を `C0CDULTS7FX` に招待しておく。
- cooklog の通信は、Slack の Socket Mode、対応するレシピサイトへの HTTPS、`api-hub:3000` だけにする（NetworkPolicy）。

| ID | 内容 |
|---|---|
| 4-1 | shared-postgresql に DB `cooklog` と認証情報を追加（定義だけを先にマージ） |
| 4-2 | Rust のサービスを実装（取り込み・API・Socket Mode・migration）。共通の Rust 検証 workflow を使う |
| 4-3 | 既存記事 8 件を `--import-markdown` で取り込み、件数と材料が一致することを確認する |
| 4-4 | dashboard に `sections/cook/`（一覧、検索、詳細、URL からの取り込み、手入力）を追加し、`/cook/api/` の HTTPRoute を追加する |
| 4-5 | api-hub の Bot を `C0CDULTS7FX` に招待（管理者）。Slack の受信を新サービスへ切り替え、cooklog-slack を止める（受信用の Slack App は同じものを使う） |
| 4-6 | 旧ホスト名 `cooklog.internal.qroksera.com` の HTTPRoute と Split DNS を削除する |
| 4-7 | Eleventy、`Dockerfile.slack`、nginx の設定、GitHub へ push するための Deploy Key / token と ExternalSecret を削除する |

完了条件: 1〜4 の要件をすべて満たす。Slack に URL を投稿すると数秒で dashboard に出て、材料名で検索できる。

### Phase 5 後片付け

| ID | 内容 |
|---|---|
| 5-1 | 旧ホスト名への参照が残っていないことを全リポジトリで確認する |
| 5-2 | docs と `AGENTS.md` を最終の構成に合わせる |
| 5-3 | 再測定（メモリ requests の増減を記録） |

## 5. 判断記録

| No | 判断事項 | 選択肢 | 推奨 |
|---|---|---|---|
| 1 | dashboard リポジトリの公開範囲 | public / private | **決定（2026-11-25）**: private |
| 2 | cooklog の Slack への返信 | api-hub `/slack/message` でスレッドに返す / `/notifications` に `thread_ts` を足す | **決定（2026-11-25）**: `/slack/message`。チャンネル `C0CDULTS7FX`、Bot は api-hub のものでよい |
| 3 | 旧ホスト名のリダイレクト | 2 週間 / 恒久 / なし | **決定（2026-11-25）**: なし。切り替えと同時に旧ホスト名を消す |
| 4 | cookLog リポジトリの扱い | 同じリポジトリで作り直す / 新リポジトリ | **決定（2026-11-25）**: 同じリポジトリ |

## 6. リスク

| リスク | 対策 |
|---|---|
| CORS の設定漏れで PDF・動画が開けない | Phase 1 では新旧両方を許可してから切り替える。1-5 で実機確認する |
| URLRewrite が NGINX Gateway Fabric で想定どおりに動かない | 3-1 を Phase 3 の最初に単独で確かめる |
| dashboard の停止でリモコンも使えなくなる | 外部依存を持たせない。dashboard は静的配信だけで、Harbor が止まってもキャッシュ済みのイメージで起動する（H-1） |
| レシピの取り込み先サイトの HTML 変更 | JSON-LD を優先し、失敗は Slack に返す。今と同じリスクで、悪化はしない |
