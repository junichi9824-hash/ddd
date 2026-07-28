# AI動向 朝の要約ダイジェスト

毎朝(JST 3:00)、過去24時間分のAI関連ニュースをGitHub Actionsで自動収集し、
Claude Haikuで最大5件に厳選・要約したうえで、「今日の一言」(AI用語解説)を添えて
Gmail経由でメール送信するシステムです。

## 特徴

- **低コスト**: Batch APIは使わず `claude-haiku-4-5-20251001` の通常APIを1日1回呼び出すのみ。月$1〜2程度を想定(月500円以内の予算に収まる)。
- **重複排除**: 一度取り上げた記事URL・解説済みの用語は `data/topics_log.json` に記録し、再掲を避ける。
- **速報性重視**: 深掘りはせず1〜2行要約+出典URLのみ。気になったものは自分でURLにアクセスして確認する運用。

## 情報源

| 情報源 | 取得方法 |
| --- | --- |
| Anthropic公式ブログ | `sitemap.xml` の `lastmod` から新着 `/news/` ページを検出(公式RSSが存在しないため) |
| OpenAI公式ブログ | RSS (`https://openai.com/news/rss.xml`) |
| Google DeepMind公式ブログ | RSS (`https://deepmind.google/blog/rss.xml`) |
| Hacker News | Algolia Search API (`AI` `LLM` `Claude` `GPT` `Gemini` などのキーワード、直近24時間・一定スコア以上) |
| arXiv cs.CL / cs.AI 新着 | 公式RSS (`export.arxiv.org/rss/...`) |

外部サイトのRSS/API仕様は将来変更される可能性があるため、動作しなくなった場合は
`digest.py` 冒頭の `RSS_FEEDS` / `ANTHROPIC_SITEMAP_URL` / `HN_*` 定数を見直してください。

## セットアップ

### 1. リポジトリのSecrets設定(GitHub Actionsで実行する場合)

GitHubリポジトリの `Settings > Secrets and variables > Actions` で以下を登録してください。
**APIキーやアプリパスワードは絶対にコードやREADMEに直接書かないこと。**

| Secret名 | 内容 |
| --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic Consoleで発行したAPIキー |
| `GMAIL_ADDRESS` | 送信元Gmailアドレス |
| `GMAIL_APP_PASSWORD` | Googleアカウントの「アプリパスワード」(2段階認証を有効にした上で発行) |
| `MAIL_TO` | 送信先メールアドレス(省略時は `GMAIL_ADDRESS` 宛) |

アプリパスワードの発行方法: Googleアカウント設定 > セキュリティ > 2段階認証プロセス > アプリパスワード。

### 2. ローカルでのテスト実行

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:GMAIL_ADDRESS = "your-address@gmail.com"
$env:GMAIL_APP_PASSWORD = "xxxxxxxxxxxxxxxx"
$env:MAIL_TO = "your-address@gmail.com"
python digest.py
```

環境変数はターミナルに直接貼り付けず、可能であれば1Passwordやローカルの `.env` (Git管理外)経由での設定を推奨します。

### 3. GitHub Actionsでの自動実行

`.github/workflows/daily-digest.yml` が毎日 UTC 18:00 (JST 3:00) に自動実行します。
`workflow_dispatch` にも対応しているため、Actionsタブから手動実行してテストも可能です。

実行後、`data/topics_log.json` の更新差分は自動でコミット・pushされます。

## ファイル構成

```
.
├── digest.py                          # メインスクリプト
├── requirements.txt                   # Python依存パッケージ
├── data/topics_log.json               # 既出URL・用語・カテゴリローテーションの記録
├── .github/workflows/daily-digest.yml # 自動実行ワークフロー
├── setup.ps1                          # 自宅PC用の開発環境自動構築スクリプト
└── README.md
```

## カスタマイズ

- **情報源の追加/変更**: `digest.py` の `RSS_FEEDS` タプルリストに追記。
- **「今日の一言」のカテゴリ**: `WORD_CATEGORIES` リストを編集(順番にローテーション)。
- **配信頻度・時刻**: `.github/workflows/daily-digest.yml` の `cron` を変更。
- **1日あたりの厳選件数**: `digest.py` の `MAX_PICKS` を変更。

## スコープ外

- X(旧Twitter)への自動投稿などのアウトプット施策は現時点ではスコープ外。まずはメール受診(インプット)に集中する。
