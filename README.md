# AI活用・DX 朝の要約ダイジェスト

毎朝(JST 3:17)、国内のIT/ビジネスメディアから「AIを使った業務効率化・活用事例」と
「企業がAIを扱ううえでのリスク・権利・規制」の記事をGitHub Actionsで自動収集し、
**記事本文を読んだうえで** Claude Haiku が要約、Gmail経由でメール送信するシステムです。

## メールの構成(読了5分程度)

| セクション | 内容 |
| --- | --- |
| 📊 AI活用事例(最大3件) | 誰が/対象業務/方法/効果/中小企業で試すなら + 出典URL |
| ⚖️ リスク・ルール(1件) | 著作権・個人情報・情報漏えい・規制などのニュース。該当記事がない日は基礎テーマをローテーションで解説(AI生成の一般解説である旨を明記) |
| 📰 その他の注目記事(最大3件) | 1行紹介 + 出典URL |

深掘りはせず要約のみ。気になったものはリンク先で確認する運用です。

## コスト

- モデル: `claude-haiku-5-5`(effort=low)
- **Message Batches API(50%割引)** で処理。60分以内に終わらなければキャンセルして通常APIにフォールバック
- 本文は1記事3,000字・最大15記事に制限し、Haiku 5.5の低単価帯(プロンプト10万トークン以下)に収める
- 目安: 月数十円程度(旧構成のHaiku 4.5通常APIと比べ大幅減)。実際の使用量はActionsログの「トークン使用量」で確認できます

## 情報源

| 情報源 | 取り込み条件 |
| --- | --- |
| ITmedia AI+ | 全件 |
| ITmedia エンタープライズ | AI関連キーワードを含む記事のみ |
| ITmedia ビジネスオンライン | 同上 |
| ITmedia NEWS セキュリティ | 同上 |
| ZDNET Japan | 同上 |

本文は `trafilatura` で抽出し、本文が取れなかった記事はAIに渡しません(タイトルだけで要約させると内容を推測で埋めるため)。
フィードのURLが変わって取得できなくなった場合は、Actionsログの `[WARN] RSS取得失敗` を確認し `digest.py` の `RSS_FEEDS` を更新してください。

## 失敗時の通知

処理中にエラーが起きた場合は「⚠️ 生成失敗」メールが届きます。詳細はGitHub Actionsのログを確認してください。

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

`.github/workflows/daily-digest.yml` が毎日 UTC 18:17 (JST 3:17) に自動実行します。
`workflow_dispatch` にも対応しているため、Actionsタブから手動実行してテストも可能です。

実行後、`data/topics_log.json` の更新差分は自動でコミット・pushされます。

## ファイル構成

```
.
├── digest.py                          # メインスクリプト
├── requirements.txt                   # Python依存パッケージ
├── data/topics_log.json               # 既出URL・リスク基礎テーマのローテーション位置
├── .github/workflows/daily-digest.yml # 自動実行ワークフロー
├── setup.ps1                          # 自宅PC用の開発環境自動構築スクリプト
└── README.md
```

## カスタマイズ

- **情報源の追加/変更**: `digest.py` の `RSS_FEEDS` に追記(3つ目の値をTrueにするとAIキーワードで絞り込み)
- **リスク基礎テーマ**: `RISK_THEMES` を編集(ニュースがない日に順番に使用)
- **件数**: `MAX_CASES` / `MAX_OTHERS`
- **配信時刻**: `.github/workflows/daily-digest.yml` の `cron`
