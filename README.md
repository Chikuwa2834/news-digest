# newsbot — ニュース自動収集と日本語ダイジェスト

RSS/Atom フィードからニュースを集め、AI で日本語のタイトルと要約を付けて Gmail に送ります。
AI は既定で Gemini API の無料枠を使います。`config.yaml` の `llm.provider` を `claude` にすると Claude API（有料）に切り替わります。

- **ダイジェスト**: `config.yaml` の `digest.times`（既定は 7:00 と 19:00）に、前回以降の新着をまとめて 1 通送る
- **速報**: `keywords.yaml` の語を含む新着が出たら、次の収集（既定では 10 分ごと）ですぐ 1 通送る
- メールの記事タイトルは元記事へのリンク。英語記事には原題も併記する

## 初期設定

1. `.env` を編集する
   - `GMAIL_ADDRESS`: 送信に使う Gmail アドレス
   - `GMAIL_APP_PASSWORD`: Google アカウント → セキュリティ → 2 段階認証プロセス → アプリ パスワード で発行した 16 文字
   - `GEMINI_API_KEY`: 翻訳・要約用。https://aistudio.google.com/apikey で無料発行。空欄でも原文のまま動く
   - `ANTHROPIC_API_KEY`: `provider: claude` のときだけ必要
   - `MAIL_TO`: 送り先。空欄なら `GMAIL_ADDRESS` に届く
2. 動作確認
   ```
   uv run newsbot test-mail          # テストメール
   uv run newsbot digest --dry-run   # data/preview.html にダイジェストを保存（送信しない）
   uv run newsbot digest             # 今すぐダイジェストを送る
   ```
3. 自動実行を登録する（Windows タスクスケジューラ、ログオン中に 10 分ごと）
   ```
   uv run newsbot schedule install
   ```
   止めるときは `uv run newsbot schedule remove`。

## GitHub Actions で 24 時間動かす

`.github/workflows/newsbot.yml` が 10 分ごとに `newsbot run` を実行します。PC の電源は不要です。
ダイジェストの配信時刻は日本時間で解釈されます（`TZ: Asia/Tokyo`）。

- **Secrets**（Settings → Secrets and variables → Actions）に `GMAIL_ADDRESS`・`GMAIL_APP_PASSWORD`・`GEMINI_API_KEY`（必要なら `MAIL_TO`）を登録する。`gh` を使うなら `gh secret set -f .env`
- Gemini の無料枠（Flash-Lite で 1 日 500 回程度）に対し、使うのはダイジェスト 1 回あたり数回と速報の回数分。無料枠の入力は Google の学習に使われることがある（送るのは公開ニュースの見出しと概要だけ）
- **既読管理**は `state` ブランチに `state.db` を 1 コミットだけ置き、毎回上書きする（履歴は増えない）
- **手動実行**: Actions タブ → newsbot → Run workflow で `run` / `preview` / `digest` / `test-mail` を選べる。`preview` は送信せずにダイジェストを作り、実行結果の Artifacts に `preview.html` を置く
- **サイトやキーワードの変更**は `feeds.yaml` / `keywords.yaml` を編集して push する。GitHub の Web 画面で直接編集してもよい
- GitHub の混雑で実行が数分〜十数分遅れることがある
- 公開リポジトリでも、60 日間コミットが無いと定期実行が止まる。止まったら GitHub からメールが届くので、Actions タブで再開する
- ローカルのタスクスケジューラと併用すると二重に届くので、どちらか一方にする

## サイトの管理

`feeds.yaml` を直接編集するか、コマンドで操作します。

```
uv run newsbot feed list
uv run newsbot feed add https://gigazine.net/ --category テクノロジー   # サイト URL からフィードを自動検出
uv run newsbot feed add https://example.com/rss.xml --name "Example"
uv run newsbot feed test 3        # 3 番のフィードの最新記事を表示
uv run newsbot feed disable 3     # 休止（enable で再開）
uv run newsbot feed remove 3
```

追加したばかりのフィードは、既存の記事では速報を出しません（次の新着から）。

## 速報キーワードの管理

```
uv run newsbot kw list
uv run newsbot kw add 半導体 --words 半導体 semiconductor TSMC --exclude 求人
uv run newsbot kw disable 半導体
uv run newsbot kw remove 半導体
```

一致はタイトルと概要への部分一致（大文字小文字は無視）です。英語記事も拾うには英語表記も `--words` に並べます。

## ファイル

| ファイル | 内容 |
|---|---|
| `config.yaml` | 送り先、配信時刻、収集間隔、モデルなど |
| `feeds.yaml` | 収集対象のフィード |
| `keywords.yaml` | 速報キーワード |
| `.env` | Gmail と API キー（公開しない） |
| `data/state.db` | 既読管理（60 日で自動削除） |
| `data/newsbot.log` | 実行ログ。届かないときはまずここを見る |

`poll_interval_minutes` を変えたら `schedule install` をやり直してください。
