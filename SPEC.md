# Apple認定整備済製品 Mac mini 在庫監視システム 詳細仕様書

**バージョン:** 1.0
**作成日:** 2026-05-12
**目的:** Codex（またはAIコーディングアシスタント）への実装依頼用

---

## 1. システム概要

### 目的

Appleの認定整備済製品ページを定期的に監視し，Mac miniが新たに入荷したときだけLINEに通知するシステム。

### 特徴

- GitHub Actionsで15分ごとに自動実行（常時起動PC不要）
- 同一商品への重複通知なし
- 秘密情報・個人識別子をリポジトリに含めない設計
- 購入自動化なし・公開ページのみ対象

### スコープ外

- ログインが必要なAppleアカウント連携
- 購入・カート操作の自動化
- 複数ユーザーへの一斉配信
- Mac mini以外の製品監視（拡張は将来対応）

---

## 2. 全体アーキテクチャ

```
┌─────────────────────────────────────────────────────┐
│                  GitHub Actions                      │
│  ┌──────────┐    ┌───────────────┐    ┌───────────┐ │
│  │  Trigger  │───▶│  watcher.py   │───▶│  Commit   │ │
│  │(cron/手動)│    │  (Python)     │    │  JSON更新 │ │
│  └──────────┘    └───────┬───────┘    └───────────┘ │
│                          │                           │
│            ┌─────────────┼──────────────┐            │
│            ▼             ▼              ▼            │
│     Apple公開ページ  notified_      LINE Messaging  │
│     (HTTP GET)      items.json      API (Push)      │
└─────────────────────────────────────────────────────┘

シークレット管理:
  GitHub Secrets → 環境変数として注入（コードには書かない）
  - LINE_CHANNEL_ACCESS_TOKEN
  - LINE_USER_ID
```

---

## 3. 処理フロー

```
START
  │
  ▼
[1] Apple整備済製品ページをHTTP GETで取得
  │
  ├─ 失敗（接続エラー・タイムアウト）
  │     └─▶ エラーログ出力 → EXIT(1)（通知はしない）
  │
  ▼
[2] HTMLまたは埋め込みJSONからMac mini商品一覧を抽出
  │
  ├─ 商品0件（ページ構造変化の可能性）
  │     └─▶ 警告ログ出力 → EXIT(0)（正常終了）
  │
  ▼
[3] data/notified_items.json を読み込む
  │     （ファイルが存在しない場合は空リストとして扱う）
  │
  ▼
[4] 取得した商品リスト と 通知済みリスト を比較
  │     比較キー：商品URL（最も安定した識別子）
  │
  ▼
[5] 新規商品（通知済みリストにないURL）を抽出
  │
  ├─ 新規商品なし → EXIT(0)（何もしない）
  │
  ▼
[6] 新規商品ごとにLINE Push通知を送信
  │
  ▼
[7] 通知済みリストに新規商品を追加
      保存内容：url, product_id, name, price, detected_at
      ※ 秘密情報・個人識別子は一切含めない
  │
  ▼
[8] data/notified_items.json を更新
  │
  ▼
[9] GitHub Actionsがファイル変更を検知してコミット
      コミットメッセージ例：
      "chore: update notified items [skip ci]"
  │
  ▼
END
```

---

## 4. 監視対象URLと取得方針

### 監視対象URL

```
https://www.apple.com/jp/shop/refurbished/mac/mac-mini
```

### 取得方針（優先順位順）

#### 方針A（推奨・初期実装）：埋め込みJSONの解析

Appleの整備済製品ページは `<script type="application/json">` タグ内にNext.jsやReact用の商品データJSONを埋め込んでいる場合がある。

探索対象のパターン例：

```html
<script id="__NEXT_DATA__" type="application/json">...</script>
```

**実装方針：**

1. `requests.get()` でHTMLを取得
2. BeautifulSoupで `<script type="application/json">` タグを全て抽出
3. 各タグの内容をJSONパースし，"Mac mini" を含む商品データ構造を探索
4. 見つかった場合はそこから商品名・価格・URLを取得

#### 方針B（フォールバック）：HTML直接パース

BeautifulSoupで商品カードのCSSクラスやdata属性を探索。

**実装方針：**

1. 商品リスト要素（`<ul>`, `<li>` など）を特定
2. 商品名テキスト，価格テキスト，リンクURLを抽出
3. "Mac mini" を含む要素だけを保持

#### 方針C（最終手段）：Playwright使用

JavaScriptレンダリングが必須でA・Bが機能しない場合のみ使用。

> **注意：** GitHub Actionsでの実行コストが増加するため，A・Bで取得できない場合のみ切り替える。

```python
# playwright install chromium をActions workflowに追加
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(TARGET_URL, wait_until="networkidle")
    html = page.content()
    browser.close()
```

#### ページ構造変化への対応

- 商品が0件かつHTTPステータスが200の場合 → 警告ログ出力・正常終了
- スクリプト内容が取得できない・JSON解析失敗 → 方針Bへフォールバック
- 方針B失敗 → `WARN: Page structure may have changed` ログ出力

### アクセス設定

```python
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; refurb-watcher/1.0)",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}
REQUEST_TIMEOUT = 30  # 秒
```

---

## 5. 商品検出ロジック

### 抽出する商品情報

| フィールド | 取得元 | 例 |
|-----------|--------|-----|
| `name` | 商品名テキスト | `Mac mini 整備済製品 M4 チップ 16GBメモリ 256GB` |
| `price` | 価格テキスト | `￥99,800` |
| `url` | 商品詳細URL（絶対URL） | `https://www.apple.com/jp/shop/product/XXXXXX/...` |
| `product_id` | URLまたはdata属性から抽出 | `XXXXXX`（URLのパス部分） |
| `detected_at` | 検出日時（ISO8601） | `2026-05-12T10:30:00+09:00` |

### Mac mini フィルタ条件（初期実装）

```python
def is_mac_mini(product_name: str) -> bool:
    return "Mac mini" in product_name
```

### 将来の拡張フィルタ（コメントアウトで実装に含める）

```python
# 将来の絞り込み条件（現在は未使用）
# FILTER_CHIP = "M4"             # チップ種別
# FILTER_MIN_MEMORY_GB = 16      # 最小メモリ (GB)
# FILTER_MIN_SSD_GB = 512        # 最小SSD (GB)
# FILTER_TEN_GBE = False         # 10ギガビットEthernet必須
# FILTER_MAX_PRICE = 200000      # 価格上限 (円)
```

---

## 6. 新規入荷判定ロジック

### 比較キー

商品URLを一意識別子として使用する。

**理由：**
- URLは商品ごとに固有（SKU等を含む）
- 価格・商品名は変更される可能性がある
- product_idは取得できない場合があるため補助的に使用

### 判定アルゴリズム

```python
def find_new_items(
    current_items: list[dict],
    notified_items: list[dict]
) -> list[dict]:
    notified_urls = {item["url"] for item in notified_items}
    return [item for item in current_items if item["url"] not in notified_urls]
```

### 通知済みリストの管理方針

- 通知済みリストは**追記のみ**（削除しない）
- ページから消えた商品も通知済みとして保持する
  - 理由：売り切れ後に再入荷した場合，同URLで再通知されるべきか判断が難しいため，初期実装では保持を優先
- リストが肥大化した場合の対処：READMEに手動リセット手順を記載

---

## 7. LINE通知仕様

### 使用API

LINE Messaging API / Push Message

```
POST https://api.line.me/v2/bot/message/push
Authorization: Bearer {LINE_CHANNEL_ACCESS_TOKEN}
Content-Type: application/json
```

### リクエストボディ

```json
{
  "to": "{LINE_USER_ID}",
  "messages": [
    {
      "type": "text",
      "text": "Apple整備済製品にMac miniが入荷しました。\n\n商品名：\n{name}\n\n価格：\n{price}\n\nURL：\n{url}\n\n検出日時：\n{detected_at}"
    }
  ]
}
```

### 通知条件

- 新規商品が1件以上検出された場合のみ送信
- 1回の実行で複数商品が検出された場合，商品ごとに1通ずつ送信
- 既に通知済みの商品には送信しない

### セキュリティ要件

- `LINE_CHANNEL_ACCESS_TOKEN` および `LINE_USER_ID` は環境変数から取得
- レスポンスのログ出力時にAuthorizationヘッダを含めない
- エラー時もトークン・userIdをログに出力しない

```python
# NG例（禁止）
print(f"Sending to user: {user_id}")
print(f"Token: {token}")

# OK例
logger.info("Sending LINE notification for new item.")
logger.error("LINE API request failed. Status: %d", response.status_code)
```

### エラー時の挙動

| 状況 | 挙動 |
|------|------|
| 401 Unauthorized | ログに「認証エラー」と記録，EXIT(1) |
| 429 Too Many Requests | ログに「レート制限」と記録，EXIT(1) |
| その他4xx/5xx | ログにステータスコードのみ記録，EXIT(1) |
| タイムアウト | ログに「タイムアウト」と記録，EXIT(1) |

---

## 8. 状態管理仕様

### 方式比較

| 方式 | メリット | デメリット | 評価 |
|------|---------|-----------|------|
| **①リポジトリ内JSONファイル** | シンプル・追加設定不要・履歴が残る | コミット権限が必要・コミット履歴が増える | ★ **採用** |
| ②GitHub Actions Cache | 設定が少ない | TTLがあり消える可能性（7日） | △ |
| ③GitHub Artifacts | 永続性あり | ダウンロード・アップロード処理が複雑 | △ |
| ④GitHub Gist | リポジトリ外で管理 | Gist IDの管理が必要 | △ |

**→ 初期実装は①を採用**

### `data/notified_items.json` の仕様

#### フォーマット

```json
[
  {
    "url": "https://www.apple.com/jp/shop/product/XXXXXX/mac-mini-...",
    "product_id": "XXXXXX",
    "name": "Mac mini 整備済製品 M4チップ 16GBメモリ 256GB SSD",
    "price": "￥99,800",
    "detected_at": "2026-05-12T10:30:00+09:00"
  }
]
```

#### 保存するフィールド（公開情報のみ）

| フィールド | 説明 |
|-----------|------|
| `url` | 商品詳細URL（公開ページ） |
| `product_id` | URLから抽出した商品識別子 |
| `name` | 商品名（公開情報） |
| `price` | 価格（公開情報） |
| `detected_at` | 検出日時（システム生成） |

#### 絶対に保存しないフィールド

- LINEユーザーID
- アクセストークン・チャネルシークレット
- メールアドレス・氏名
- IPアドレスなど端末固有情報

#### 初期状態

```json
[]
```

リポジトリには空配列の `data/notified_items.json` をコミットする。

### GitHub Actionsによる自動コミット

```yaml
- name: Commit updated notified items
  run: |
    git config user.name "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"
    git add data/notified_items.json
    git diff --staged --quiet || git commit -m "chore: update notified items [skip ci]"
    git push
```

**注意点：**

- `[skip ci]` を付けてワークフローの無限ループを防ぐ
- `git diff --staged --quiet` で変更がない場合はコミットしない
- `user.email` は GitHub の noreply アドレスを使用（個人情報を含まない）

---

## 9. GitHub Actions仕様

### ワークフローファイル: `.github/workflows/watch.yml`

```yaml
name: Mac mini Refurb Watcher

on:
  schedule:
    - cron: "*/15 * * * *"   # 15分ごと（UTC）
  workflow_dispatch:           # 手動実行

permissions:
  contents: write              # JSONコミットのため書き込み権限が必要

jobs:
  watch:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 1

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run watcher
        env:
          LINE_CHANNEL_ACCESS_TOKEN: ${{ secrets.LINE_CHANNEL_ACCESS_TOKEN }}
          LINE_USER_ID: ${{ secrets.LINE_USER_ID }}
        run: python src/watcher.py

      - name: Commit updated notified items
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/notified_items.json
          git diff --staged --quiet || git commit -m "chore: update notified items [skip ci]"
          git push
```

### 設定値の根拠

| 設定 | 値 | 理由 |
|------|----|------|
| `permissions: contents: write` | write | JSONファイルをコミットするため |
| `timeout-minutes: 10` | 10 | 無限ループ防止 |
| `fetch-depth: 1` | 1 | 高速化（履歴不要） |
| `cache: "pip"` | pip | 依存インストール高速化 |
| `[skip ci]` | コミットメッセージに付与 | ワークフローの無限起動防止 |

### セキュリティ考慮

- SecretsはGitHub Secretsから環境変数として注入する
- `env:` ブロックで渡すことでActionsログへの直接出力を防ぐ
- `run:` ステップ内で `echo $LINE_CHANNEL_ACCESS_TOKEN` などを実行しない
- デバッグ目的の環境変数ダンプ（`env` コマンド，`printenv`）を実行しない

---

## 10. 必要な環境変数・Secrets

### GitHub Secrets（必須）

| Secret名 | 説明 | 取得方法 |
|----------|------|---------|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging APIのチャネルアクセストークン（長期） | LINE Developersコンソール → チャンネル設定 → Messaging API |
| `LINE_USER_ID` | 通知先LINEユーザーのUser ID | LINE Developersコンソール → チャンネル設定 → Messaging API → 「あなたのユーザーID」，または友達追加後にWebhookイベントから取得 |

### ローカル開発用 `.env`（gitignore対象）

```env
LINE_CHANNEL_ACCESS_TOKEN=your_channel_access_token_here
LINE_USER_ID=your_line_user_id_here
```

### `.env.example`（リポジトリにコミット可能）

```env
LINE_CHANNEL_ACCESS_TOKEN=YOUR_CHANNEL_ACCESS_TOKEN_HERE
LINE_USER_ID=YOUR_LINE_USER_ID_HERE
```

---

## 11. ファイル構成

```
apple-refurb-macmini-watcher/
├── .github/
│   └── workflows/
│       └── watch.yml          # GitHub Actionsワークフロー定義
├── src/
│   └── watcher.py             # メインスクリプト
├── data/
│   └── notified_items.json    # 通知済み商品リスト（公開情報のみ）
├── requirements.txt           # Python依存ライブラリ
├── README.md                  # セットアップ・運用手順
├── .env.example               # 環境変数サンプル（ダミー値のみ）
└── .gitignore                 # .envなどを除外
```

---

## 12. 各ファイルの役割

### `src/watcher.py`

メインスクリプト。以下のモジュール構成で実装する。

```python
# 定数（ファイル先頭で定義）
TARGET_URL = "https://www.apple.com/jp/shop/refurbished/mac/mac-mini"
NOTIFIED_ITEMS_PATH = "data/notified_items.json"
REQUEST_TIMEOUT = 30
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; refurb-watcher/1.0)",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

# 関数一覧（型ヒント必須）
def fetch_page(url: str) -> str: ...
def extract_from_json_script(html: str) -> list[dict] | None: ...
def extract_from_html(html: str) -> list[dict]: ...
def extract_mac_mini_items(html: str) -> list[dict]: ...
def load_notified_items(path: str) -> list[dict]: ...
def save_notified_items(path: str, items: list[dict]) -> None: ...
def find_new_items(current: list[dict], notified: list[dict]) -> list[dict]: ...
def format_message(item: dict) -> str: ...
def send_line_notification(item: dict, token: str, user_id: str) -> None: ...
def main() -> None: ...
```

### `.github/workflows/watch.yml`

GitHub Actionsのワークフロー定義。スケジュール・手動実行・Secrets注入・JSONコミットを担う。

### `data/notified_items.json`

通知済み商品の記録ファイル。**公開情報のみ**を保存。初期状態は `[]`。

### `requirements.txt`

```
requests>=2.31.0
beautifulsoup4>=4.12.0
python-dotenv>=1.0.0
```

### `.gitignore`

```gitignore
# 環境変数（秘密情報を含む）
.env
.env.local

# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/

# OS
.DS_Store
Thumbs.db

# ログ
*.log
```

### `.env.example`

```env
# LINE Messaging API設定
# 実際の値はGitHub Secretsまたはローカルの.envに設定してください
LINE_CHANNEL_ACCESS_TOKEN=YOUR_CHANNEL_ACCESS_TOKEN_HERE
LINE_USER_ID=YOUR_LINE_USER_ID_HERE
```

---

## 13. エラーハンドリング方針

### エラー種別と対応

| エラー種別 | 対応 | 終了コード |
|-----------|------|-----------|
| ページ取得失敗（接続エラー・タイムアウト） | ログ出力 | EXIT(1) |
| HTTP 4xx/5xx（Appleサーバー） | ログ出力 | EXIT(1) |
| HTMLパース失敗・商品0件 | 警告ログ（正常終了） | EXIT(0) |
| JSONファイル読み込み失敗 | 空リストで代替・警告ログ | 処理継続 |
| JSONファイル書き込み失敗 | エラーログ | EXIT(1) |
| LINE API認証エラー(401) | エラーログ（トークン内容は出さない） | EXIT(1) |
| LINE APIレート制限(429) | エラーログ | EXIT(1) |
| LINE APIその他エラー | ステータスコードのみログ | EXIT(1) |
| 環境変数未設定 | 起動時に検証・エラーログ | EXIT(1) |

### ログレベル基準

```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
```

| レベル | 用途 |
|--------|------|
| INFO | 正常処理（取得件数，通知件数など） |
| WARNING | 商品0件，JSONファイルなし等 |
| ERROR | API失敗，ファイルI/Oエラー |

### ログに絶対に含めてはいけない情報

- LINE_CHANNEL_ACCESS_TOKEN の値
- LINE_USER_ID の値
- LINE APIのレスポンスヘッダ全体
- HTTPリクエストの Authorization ヘッダ

---

## 14. セキュリティ・運用上の注意

### 秘密情報の保存場所ルール

| 保存場所 | 保存してよいもの |
|---------|----------------|
| GitHub Secrets | トークン・userIdのみ |
| `.env`（ローカル） | トークン・userIdのみ |
| `.gitignore` | `.env` を必ず含める |

**絶対に置いてはいけない場所：**
- コードファイル (`.py`)
- `README.md`
- `.env.example`（ダミー値のみ可）
- `data/notified_items.json`
- GitHub Actions ログ
- コミットメッセージ

### 誤ってSecretsをコミットした場合の対処

1. **即座にトークンを無効化・再発行する**（削除だけでは不十分）
   - LINE Developers コンソールでチャネルアクセストークンを再発行
2. git履歴から削除する（`git filter-repo` または BFG Repo Cleaner を使用）
3. GitHub Secretsの値を新しいトークンに更新する
4. 公開リポジトリの場合，一時的にprivateに変更することを検討する

> **重要:** APIキーやアクセストークンを誤って公開した場合，履歴から削除するだけでは不十分です。
> 必ず該当トークンを**無効化・再発行**してください。

### Appleサーバーへの配慮

- アクセス頻度：最短15分に1回
- User-Agentを明示する（ボット名を含む）
- HTTPレスポンスのStatusが429・503の場合はリトライしない

---

## 15. READMEに書くべき内容

```
1. システム概要
2. 動作の仕組み
3. ファイル構成
4. セットアップ手順
   4-1. 前提条件
   4-2. LINE Messaging APIの準備
        - LINE Developersアカウント作成
        - チャネル作成（Messaging API）
        - チャネルアクセストークン（長期）の発行
        - 自分のLINE User IDの確認方法
        - Messaging APIチャンネルのBotを友達追加する手順
   4-3. リポジトリのクローン・フォーク手順
   4-4. GitHub Secretsの設定
        - LINE_CHANNEL_ACCESS_TOKEN の設定方法
        - LINE_USER_ID の設定方法
5. ローカルでの実行方法
   - .envファイルの作成（.env.exampleをコピー）
   - .envには実値を記入（.gitignoreで除外済み）
   - python src/watcher.py の実行
6. GitHub Actionsでの実行方法（自動・手動）
7. 通知済み商品のリセット方法
   - data/notified_items.json を [] に戻してコミット
8. 注意事項
9. セキュリティについて（重要）
   - .envは絶対にコミットしないこと
   - .env.exampleにはダミー値のみを記載すること
   - GitHub ActionsのログにSecretsが出ないようにするための注意
   - data/notified_items.jsonには公開情報のみを保存すること
   - 誤ってAPIキー・トークンをコミットした場合の対処方法
     （削除だけでなく必ず無効化・再発行する）
10. トラブルシューティング
    - 通知が来ない場合
    - ページ構造変化への対応
    - Actions実行失敗時の確認手順
```

---

## 16. Codexへの実装指示

### 実装の優先順位

| Phase | 内容 | 必須 |
|-------|------|------|
| Phase 1 | ページ取得→Mac mini抽出→通知済み比較→LINE通知→JSON保存 | ✅ |
| Phase 1 | GitHub Actionsワークフロー | ✅ |
| Phase 1 | エラーハンドリング・ログ出力 | ✅ |
| Phase 2 | 絞り込みフィルタ（チップ・メモリ・SSD・価格） | 任意 |
| Phase 3 | Playwrightフォールバック | 任意 |

### コーディング規約

- Python 3.12以上を想定
- 型ヒントを使用する（`list[dict]`, `str | None` 等）
- `main()` 関数を定義し，`if __name__ == "__main__": main()` で呼び出す
- モジュールレベルの定数はファイル先頭に定義する
- ハードコードされたURLや設定値は定数として切り出す

---

## 17. 実装後のテスト項目

```
[ ] ローカルで python src/watcher.py が正常終了する
[ ] Apple整備済ページからMac miniが1件以上取得できる
[ ] data/notified_items.json が正しい形式で保存される
[ ] 2回目の実行で同じ商品の重複通知がない
[ ] LINEに通知メッセージが届く
[ ] 通知メッセージに商品名・価格・URL・検出日時が含まれる
[ ] data/notified_items.json に秘密情報・個人識別子が含まれていない
[ ] .env が git status に表示されない（.gitignore 確認）
[ ] .env.example にダミー値のみが含まれる
[ ] GitHub Actions で手動実行（workflow_dispatch）が成功する
[ ] GitHub Actions のログに SECRET 値が表示されていない
[ ] Actions 完了後，data/notified_items.json の変更がコミットされている
[ ] 商品0件の場合（URLを一時的に変更等）エラーにならず正常終了する
[ ] LINE API 認証エラー時に適切なエラーログが出る（値は出ない）
[ ] data/notified_items.json を [] に戻すと再度通知される
```
