# Apple Refurbished Mac mini Watcher

Apple認定整備済製品ページを定期的に確認し，Mac mini の新規入荷を LINE Messaging API で通知する監視システムです．GitHub Actions で15分ごとに実行し，通知済み商品は `data/notified_items.json` に保存して重複通知を防ぎます．

## 仕組み

1. Apple の整備済 Mac mini ページを HTTP GET で取得します．
2. 埋め込み JSON を優先して解析し，失敗した場合は HTML を直接解析します．
3. `Mac mini` を含む商品だけを抽出します．
4. 通知済み URL と比較し，新規商品だけ LINE に通知します．
5. 通知後，公開情報だけを `data/notified_items.json` に保存します．

## ファイル構成

```text
.
├── .github/workflows/watch.yml
├── src/watcher.py
├── data/notified_items.json
├── requirements.txt
├── README.md
├── .env.example
└── .gitignore
```

## セットアップ

### 前提条件

- Python 3.12 以上
- GitHub Actions を使える GitHub リポジトリ
- LINE Developers の Messaging API チャンネル

### LINE Messaging API の準備

1. LINE Developers で Messaging API チャンネルを作成します．
2. チャネルアクセストークン（長期）を発行します．
3. 通知先の LINE User ID を確認します．
4. Messaging API チャンネルの Bot を通知先アカウントで友だち追加します．

### GitHub Secrets

リポジトリの `Settings` → `Secrets and variables` → `Actions` に以下を登録します．

```text
LINE_CHANNEL_ACCESS_TOKEN
LINE_USER_ID
```

値は Secrets またはローカルの `.env` のみに保存してください．コード，README，ログ，JSON には実値を書かないでください．

## ローカル実行

依存関係をインストールします．

```bash
pip install -r requirements.txt
```

`.env.example` を参考に `.env` を作り，実値を設定します．`.env` は `.gitignore` で除外されています．

```env
LINE_CHANNEL_ACCESS_TOKEN=your_channel_access_token_here
LINE_USER_ID=your_line_user_id_here
```

実行します．

```bash
python src/watcher.py
```

## GitHub Actions

`.github/workflows/watch.yml` により，15分ごとのスケジュール実行と手動実行に対応しています．手動実行は GitHub の `Actions` タブから `Mac mini Refurb Watcher` を選び，`Run workflow` を押します．

ワークフローは実行後に `data/notified_items.json` の変更があれば自動コミットします．コミットメッセージには `[skip ci]` を含め，連続実行を防ぎます．

### テスト通知

実在庫がない状態でも，スクレイピング処理と LINE 通知を検証できます．

GitHub の `Actions` タブで `Mac mini Refurb Watcher` を選び，`Run workflow` を押すときに `test_mode` を有効にします．テストモードでは `tests/fixtures/macmini_test_page.html` を解析し，抽出したテスト商品で LINE 通知を1通送ります．

テストモードでは `data/notified_items.json` は更新しません．

## 通知済み商品のリセット

再通知させたい場合は `data/notified_items.json` を次の内容に戻してコミットします．

```json
[]
```

## セキュリティ

- `.env` は絶対にコミットしないでください．
- `.env.example` にはダミー値だけを記載してください．
- GitHub Actions で `env` や `printenv` などを実行しないでください．
- `data/notified_items.json` には商品 URL，商品 ID，商品名，価格，検出日時だけを保存します．
- トークンや User ID を誤って公開した場合は，削除だけでなく，LINE Developers で該当トークンを無効化・再発行してください．

## トラブルシューティング

### 通知が来ない

- Bot が通知先アカウントで友だち追加されているか確認してください．
- GitHub Secrets の2項目が設定されているか確認してください．
- `data/notified_items.json` に同じ URL がすでに保存されていないか確認してください．

### 商品が検出されない

Apple 側のページ構造が変わった可能性があります．Actions ログで `No Mac mini items detected` が出ている場合は，`src/watcher.py` の JSON 解析または HTML 解析ロジックの更新が必要です．

### Actions が失敗する

- 依存関係のインストールが成功しているか確認してください．
- Apple への HTTP アクセスや LINE API のステータスコードを確認してください．
- ログに Secrets の値が出ない範囲で原因を確認してください．
