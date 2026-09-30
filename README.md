# ai_news_slack

毎朝 8:30 (JST) に、AI 系ニュースを **日本語要約 + リンク付き** で Slack に投稿するボットです。

```
RSS (TechCrunch / The Verge / OpenAI / Google / HN / ITmedia など)
   └─ 直近24時間の記事を収集
        └─ Claude が重要記事を最大8件選定 → 日本語で見出し・要約
             └─ Slack Incoming Webhook で投稿
```

GitHub Actions で動くので、サーバーは不要です。

## セットアップ

### 1. Slack の Incoming Webhook を作る

1. https://api.slack.com/apps → **Create New App** → From scratch
2. **Incoming Webhooks** を ON → **Add New Webhook to Workspace**
3. 投稿先を選ぶ（自分専用にしたい場合は、自分だけのチャンネル（例: `#ai-news`）を作るか、自分宛ての DM を選ぶ）
4. 発行された `https://hooks.slack.com/services/...` をコピー

### 2. Anthropic API キーを用意

https://console.anthropic.com/ で API キーを発行します。

### 3. GitHub Secrets に登録

リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で以下を登録します。

| Name | 値 |
| --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic の API キー |
| `SLACK_WEBHOOK_URL` | Slack Webhook URL |

### 4. 動作確認

**Actions → Daily AI News to Slack → Run workflow** で手動実行すると、すぐに投稿されます。
以降は毎朝自動で投稿されます。

> ⚠️ スケジュール実行はデフォルトブランチ（`main`）上のワークフローでしか動きません。このブランチをマージしてから有効になります。

## 仕組みの補足

- **投稿時刻**: GitHub Actions の cron は数分〜十数分遅れることがあるため、08:15 に起動 → 要約を作成 → 08:30 まで待ってから投稿します。混雑時は 08:30 を過ぎることがあります。
- **リンク**: URL は Claude の出力ではなく、元の RSS のものを使います（URL の捏造を防ぐため）。
- **コスト目安**: 1 日 1 回、数十記事分の入力なので Claude API は 1 回あたり数円〜十数円程度です。

## カスタマイズ

| 変更したいこと | 場所 |
| --- | --- |
| ニュースソースの追加・削除 | `ai_news.py` の `FEEDS` |
| 選定基準・要約のトーン | `ai_news.py` の `SYSTEM_PROMPT` |
| 記事数 / 収集期間 | 環境変数 `MAX_ITEMS`（既定 8） / `LOOKBACK_HOURS`（既定 24） |
| 投稿時刻 | `.github/workflows/daily-ai-news.yml` の `cron` と `POST_AT_JST` |

## ローカルで試す

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python ai_news.py --dry-run          # Slack に投稿せず結果を表示
SLACK_WEBHOOK_URL=... python ai_news.py   # 実際に投稿
```
