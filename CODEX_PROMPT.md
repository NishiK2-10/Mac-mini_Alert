# Codex 実装依頼プロンプト

以下の仕様書 `SPEC.md` に従って，Apple認定整備済製品のMac mini在庫監視システムを実装してください。

---

## 前提・制約（必ず守ること）

このリポジトリは **GitHubにアップロードする前提** です。
以下の情報を **コード・README・ログ・JSONファイル・コミットメッセージのいずれにも含めないこと**：

- LINE_CHANNEL_ACCESS_TOKEN の値
- LINE_USER_ID の値
- メールアドレス・氏名・個人名
- 端末固有情報・IPアドレス

これらはすべて **GitHub Secrets またはローカルの `.env`** で管理し，コード内では `os.getenv()` で参照するだけにすること。

---

## 実装するファイル一覧

```
apple-refurb-macmini-watcher/
├── .github/
│   └── workflows/
│       └── watch.yml
├── src/
│   └── watcher.py
├── data/
│   └── notified_items.json
├── requirements.txt
├── README.md
├── .env.example
└── .gitignore
```

---

## 各ファイルの実装仕様

### `src/watcher.py`

#### 定数（ファイル先頭で定義すること）

```python
TARGET_URL = "https://www.apple.com/jp/shop/refurbished/mac/mac-mini"
NOTIFIED_ITEMS_PATH = "data/notified_items.json"
REQUEST_TIMEOUT = 30
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; refurb-watcher/1.0)",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}
```

#### 環境変数

`os.getenv()` で取得する。未設定時は起動時に `sys.exit(1)`。値はログに出力しないこと。

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_USER_ID`

ローカル実行時は `python-dotenv` を使って `.env` から読み込む。

#### ロギング設定

```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S"
)
logger = logging.getLogger(__name__)
```

**ログに出力してはいけない情報：**
- LINE_CHANNEL_ACCESS_TOKEN の値
- LINE_USER_ID の値
- LINE API のレスポンスヘッダ全体
- HTTP リクエストの Authorization ヘッダ

#### 実装する関数（型ヒント必須）

**`fetch_page(url: str) -> str`**
- `requests.get()` で HTML を取得
- HEADERS と REQUEST_TIMEOUT を使用
- 失敗時は例外を raise（呼び出し元でキャッチ）

**`extract_from_json_script(html: str) -> list[dict] | None`**
- BeautifulSoup で `<script type="application/json">` タグを全て探索
- JSON パースし，"Mac mini" を含む商品データを探索
- 見つかった場合は商品リストを返す，見つからない場合は `None` を返す
- 各商品は `{"name": str, "price": str, "url": str, "product_id": str}` 形式

**`extract_from_html(html: str) -> list[dict]`**
- BeautifulSoup で HTML を直接パース
- Mac mini 商品の name・price・url を抽出
- 商品が取れない場合は空リストを返す

**`extract_mac_mini_items(html: str) -> list[dict]`**
- まず `extract_from_json_script` を試みる
- `None` の場合は `extract_from_html` にフォールバック
- "Mac mini" を含む商品のみを返す
- url は絶対 URL に正規化する（相対 URL の場合は `https://www.apple.com` を付与）

**`load_notified_items(path: str) -> list[dict]`**
- JSON ファイルを読み込んで返す
- ファイルが存在しない場合は `[]` を返す（WARNING ログを出す）

**`save_notified_items(path: str, items: list[dict]) -> None`**
- リストを JSON ファイルに保存（indent=2）
- 保存するフィールドは `url`, `product_id`, `name`, `price`, `detected_at` のみ
- 秘密情報・個人識別子は絶対に含めない

**`find_new_items(current: list[dict], notified: list[dict]) -> list[dict]`**
- `current` のうち，`notified` に URL が存在しないものを返す

**`format_message(item: dict) -> str`**
- 以下の形式で通知文を生成：

```
Apple整備済製品にMac miniが入荷しました。

商品名：
{name}

価格：
{price}

URL：
{url}

検出日時：
{detected_at}
```

**`send_line_notification(item: dict, token: str, user_id: str) -> None`**
- LINE Messaging API Push Message で通知を送信
- エンドポイント: `https://api.line.me/v2/bot/message/push`
- ログに token・user_id の値を出力しないこと
- HTTP ステータスコードのみをログに記録
- 失敗時は例外を raise

**`main() -> None`**
処理の流れ：
1. 環境変数の存在確認（未設定時は sys.exit(1)，値はログに出さない）
2. `fetch_page()` でページ取得
3. `extract_mac_mini_items()` で商品抽出
4. 商品 0 件の場合は WARNING ログを出して正常終了（EXIT 0）
5. `load_notified_items()` で通知済みリスト読み込み
6. `find_new_items()` で新規商品抽出
7. 新規商品がなければ正常終了（EXIT 0）
8. 新規商品ごとに `send_line_notification()` で通知
9. `save_notified_items()` で通知済みリスト更新
10. 各ステップのエラーを適切にキャッチ・ログ出力

エントリーポイント：
```python
if __name__ == "__main__":
    main()
```

---

### `.github/workflows/watch.yml`

```yaml
name: Mac mini Refurb Watcher

on:
  schedule:
    - cron: "*/15 * * * *"
  workflow_dispatch:

permissions:
  contents: write

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

**注意：**
- `env:` コマンドや `printenv` などによる環境変数のダンプを一切行わない
- ログに Secrets や個人識別子が出ないようにする

---

### `data/notified_items.json`

初期内容：

```json
[]
```

---

### `requirements.txt`

```
requests>=2.31.0
beautifulsoup4>=4.12.0
python-dotenv>=1.0.0
```

---

### `.env.example`

ダミー値のみを記載すること。実値は絶対に書かない。

```env
# LINE Messaging API設定
# 実際の値はGitHub Secretsまたはローカルの.envに設定してください
LINE_CHANNEL_ACCESS_TOKEN=YOUR_CHANNEL_ACCESS_TOKEN_HERE
LINE_USER_ID=YOUR_LINE_USER_ID_HERE
```

---

### `.gitignore`

以下を必ず含めること：

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

---

### `README.md`

以下のセクションを含む日本語の README を作成すること：

1. システム概要
2. 動作の仕組み
3. ファイル構成
4. セットアップ手順
   - 前提条件
   - LINE Messaging API の準備（チャネル作成・トークン発行・User ID 確認・Bot 友達追加）
   - リポジトリのクローン・フォーク手順
   - GitHub Secrets の設定（`LINE_CHANNEL_ACCESS_TOKEN`・`LINE_USER_ID` の設定方法）
5. ローカルでの実行方法
   - `.env` ファイルの作成（`.env.example` をコピーして実値を記入）
   - `.env` は `.gitignore` で除外済みであることを確認
   - `python src/watcher.py` の実行
6. GitHub Actions での実行方法（自動・手動）
7. 通知済み商品のリセット方法（`data/notified_items.json` を `[]` に戻してコミット）
8. 注意事項（Appleサーバーへの配慮・購入自動化禁止）
9. **セキュリティについて（重要）**
   - `.env` は絶対にコミットしないこと
   - `.env.example` にはダミー値のみを記載すること
   - GitHub Actions のログに Secrets が表示されないようにするための注意
   - `data/notified_items.json` には公開情報のみを保存し，個人識別子を含めないこと
   - 誤って APIキー・アクセストークンをコミットした場合の対処方法（以下を必ず明記）：
     > 削除するだけでは不十分です。必ず LINE Developers コンソールでトークンを**無効化・再発行**してください。その後，`git filter-repo` または BFG Repo Cleaner を使って git 履歴からも削除してください。
10. トラブルシューティング

---

## エラーハンドリング方針

| エラー種別 | 対応 | 終了コード |
|-----------|------|-----------|
| ページ取得失敗 | ログ出力 | EXIT(1) |
| HTTP 4xx/5xx（Apple） | ログ出力 | EXIT(1) |
| 商品0件 | 警告ログ（正常終了） | EXIT(0) |
| JSON 読み込み失敗 | 空リストで代替・警告ログ | 処理継続 |
| JSON 書き込み失敗 | エラーログ | EXIT(1) |
| LINE API 401 | エラーログ（値は出さない） | EXIT(1) |
| LINE API 429 | エラーログ | EXIT(1) |
| LINE API その他 | ステータスコードのみログ | EXIT(1) |
| 環境変数未設定 | 起動時に検証・エラーログ | EXIT(1) |

---

## 実装後の確認事項

実装が完了したら，以下をすべて確認すること：

```
[ ] src/watcher.py にトークンやユーザーIDがハードコードされていない
[ ] .env.example にダミー値のみが含まれている
[ ] .gitignore に .env が含まれている
[ ] data/notified_items.json の初期内容が [] である
[ ] watch.yml の env: ブロック以外に Secrets が参照されていない
[ ] watch.yml に環境変数をダンプするステップがない
[ ] README.md に誤ったトークンをコミットした場合の対処方法が書かれている
[ ] ログ出力の中にトークン・ユーザーIDを出す処理がない
```

---

## 詳細仕様の参照先

詳細な仕様（監視対象URL，商品検出ロジック，通知フォーマット，状態管理方針等）は
同ディレクトリの `SPEC.md` を参照してください。
