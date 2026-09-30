# ai_news_slack

毎日 **朝 8:00 / 夜 20:00 (JST)** の 2 回、AI 系ニュースを **日本語要約 + リンク付き** で Slack に投稿するボットです。
朝と夜で同じニュースは流しません。

```
RSS (TechCrunch / The Verge / The Decoder / OpenAI / Google / HN / ITmedia / 日経xTECH / Crunchbase など)
   └─ 直近24時間の記事を収集
        └─ 過去に投稿した記事を除外
             └─ Claude が重要記事を最大10件選定 → 日本語で見出し・要約・起業家視点の示唆
                  （直近に配信した話題は、別メディアの記事でも選ばない）
                  └─ Slack Incoming Webhook で投稿 → 投稿履歴を保存
```

GitHub Actions で動くので、サーバーは不要です。

## セットアップ

### 1. Slack の Incoming Webhook を作る

1. https://api.slack.com/apps → **Create New App** → From scratch
2. **Incoming Webhooks** を ON → **Add New Webhook to Workspace**
3. 投稿先を選ぶ（自分専用にしたい場合は、自分だけのチャンネル（例: `#ai-news`）を作るか、自分宛ての DM を選ぶ）
4. 発行された `https://hooks.slack.com/services/...` をコピー

### 2. Claude のトークンを発行（サブスク枠で動かす）

Claude の Pro / Max プランの枠内で動くので、API の追加料金はかかりません。
Claude Code が入っている PC のターミナルで以下を実行し、表示されたトークンをコピーします。

```bash
claude setup-token
```

> トークンの有効期限が切れたら、同じコマンドで再発行して Secret を更新してください。

### 3. GitHub Secrets に登録

リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で以下を登録します。

| Name | 値 |
| --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token` で発行したトークン |
| `SLACK_WEBHOOK_URL` | Slack Webhook URL |

### 4. 動作確認

**Actions → Daily AI News to Slack → Run workflow** で手動実行すると、すぐに投稿されます
（`edition` で朝版 / 夜版を選べます。空欄なら現在時刻から自動判定）。
`dry_run` にチェックを入れると、Slack に投稿せず結果をログに表示するだけになります（設定変更のお試し用）。
以降は毎日 朝 8:00 と夜 20:00 に自動で投稿されます。

> ⚠️ スケジュール実行はデフォルトブランチ（`main`）上のワークフローで動きます。

## 仕組みの補足

- **投稿時刻**: GitHub Actions の cron は数分〜十数分遅れることがあるため、配信の 15 分前（7:45 / 19:45）に起動 → 要約を作成 → 8:00 / 20:00 まで待ってから投稿します。混雑時は数分遅れることがあります。
- **朝と夜で内容を変える仕組み**: 投稿した記事の URL と見出しを `posted_history.json` に記録し（GitHub Actions のキャッシュで実行間に引き継ぎ、7 日で自動削除）、次の回では投稿済みの記事を除外します。さらに直近 2 日の配信見出しを Claude に渡し、別メディアが報じた同じニュースも選ばないようにしています。
- **リンク**: URL は Claude の出力ではなく、元の RSS のものを使います（URL の捏造を防ぐため）。
- **コスト**: Claude はサブスク（Pro / Max）の利用枠を使うので追加料金なし。GitHub Actions も公開リポジトリなら無料です。
  `ANTHROPIC_API_KEY` を Secrets に登録すると API 課金が優先されるので、登録しないでください。

## カスタマイズ

| 変更したいこと | 場所 |
| --- | --- |
| ニュースソースの追加・削除 | `ai_news.py` の `FEEDS` |
| 選定基準・ジャンル配分・要約のトーン | `ai_news.py` の `SYSTEM_PROMPT` |
| 記事数 / 収集期間 | 環境変数 `MAX_ITEMS`（既定 10） / `LOOKBACK_HOURS`（既定 24） |
| 投稿時刻 | `.github/workflows/daily-ai-news.yml` の `cron`（2 箇所）と `ai_news.py` の `EDITIONS` |

## ローカルで試す

```bash
pip install -r requirements.txt   # Claude Code（claude コマンド）にログイン済みであること
python ai_news.py --dry-run          # Slack に投稿せず結果を表示
SLACK_WEBHOOK_URL=... python ai_news.py   # 実際に投稿
```
