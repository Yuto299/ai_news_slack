# ai_news_slack

起業準備のための情報を、Claude が毎日集めて **日本語で整理** し、Slack の各チャンネルに投稿するボットです。
GitHub Actions で動くのでサーバーは不要。Claude は Pro / Max プランのサブスク枠で動くので、API の追加料金もかかりません。

| チャンネル | 中身 | 配信 (JST) | 主なソース |
| --- | --- | --- | --- |
| `#ai-news` | AI 業界ニュース 10 件。要約 + 起業家視点の一言 | 毎日 8:00 / 20:00 | TechCrunch / The Verge / The Decoder / OpenAI / Google / HN / ITmedia / 日経xTECH / Crunchbase など |
| `#pain-points` | 「こういうのが欲しい」「これが面倒」という声から、事業の種になりそうな課題 5 件 | 毎日 12:00 | Reddit / Ask HN / はてなブックマーク |
| `#launches` | 注目の新プロダクト 6 件と、その収益モデル・盗めるポイント | 毎日 18:00 | Product Hunt / Show HN / Launch HN |
| `#small-biz` | 個人で再現できそうなスモールビジネス事例 5 件（信頼度ラベル付き） | 毎日 21:00 | Reddit / HN / Zenn / Qiita / note |
| `#weekly-trends` | 1 週間で「お金と注目がどこに流れたか」のテーマ別まとめ | 毎週日曜 10:00 | `#ai-news` の 1 週間分 + 資金調達系ニュース |
| `#subsidies` | 起業・小規模事業向けの補助金・支援制度の新着 | 毎週 月・木 9:00 | jGrants（デジタル庁）/ ミラサポplus |

どのチャンネルも、一度投稿したものは次の回以降で繰り返しません。

## セットアップ

### 1. Slack の Incoming Webhook を作る

1. https://api.slack.com/apps で **Create New App** → **Blank app**（既存のアプリを使ってもよい）
2. **Incoming Webhooks** を ON → **Add New Webhook** で投稿先チャンネルを選ぶ
3. 発行された `https://hooks.slack.com/services/...` をコピー

チャンネルごとに Webhook を 1 つずつ作ります（同じアプリで何個でも追加できます）。

### 2. Claude のトークンを発行（サブスク枠で動かす）

Claude Code が入っている PC のターミナルで以下を実行し、表示されたトークンをコピーします。

```bash
claude setup-token
```

> トークンの有効期限が切れたら、同じコマンドで再発行して Secret を更新してください。

### 3. GitHub Secrets に登録

リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で登録します。

| Name | 値 |
| --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token` で発行したトークン |
| `SLACK_WEBHOOK_URL` | `#ai-news` の Webhook URL |
| `SLACK_WEBHOOK_PAIN_POINTS` | `#pain-points` の Webhook URL |
| `SLACK_WEBHOOK_LAUNCHES` | `#launches` の Webhook URL |
| `SLACK_WEBHOOK_SMALL_BIZ` | `#small-biz` の Webhook URL |
| `SLACK_WEBHOOK_WEEKLY_TRENDS` | `#weekly-trends` の Webhook URL |
| `SLACK_WEBHOOK_SUBSIDIES` | `#subsidies` の Webhook URL |

Webhook を登録していないチャンネルは、何もせずにスキップされます（必要なものだけ使えます）。

> `ANTHROPIC_API_KEY` は登録しないでください。登録すると API 課金が優先されます。

### 4. 動作確認

**Actions** タブで各チャンネルのワークフローを選び、**Run workflow** で手動実行するとすぐに投稿されます。
`dry_run` にチェックを入れると、Slack に投稿せず結果をログに表示するだけになります（設定変更のお試し用）。

## 仕組みの補足

- **投稿時刻**: GitHub Actions の cron は遅れたり、まれに実行自体が飛ばされたりするため、配信の 15 分前に起動して配信時刻まで待ちます。
  さらに予備の起動時刻（配信の 10 分後・40 分後）を置き、まだ投稿されていなければ予備の回で投稿します（投稿済みなら何もしません）。
- **繰り返さない仕組み**: 投稿したものの URL と見出しを履歴ファイルに記録し（GitHub Actions のキャッシュで実行間に引き継ぎ）、次の回では除外します。
  さらに直近に紹介した見出しを Claude に渡し、別メディアが報じた同じ話題も選ばないようにしています。
- **リンク**: URL は Claude の出力ではなく、元のソースのものを使います（URL の捏造を防ぐため）。
- **真偽**: `#small-biz` と `#pain-points` の元ネタは SNS・掲示板の投稿なので、内容は検証されていません。`#small-biz` には信頼度ラベル（証拠あり / 自己申告 / 要注意）を付けています。
- **コスト**: Claude はサブスク（Pro / Max）の利用枠を使うので追加料金なし。GitHub Actions も公開リポジトリなら無料です。
- **公開リポジトリの注意**: 60 日間リポジトリに更新がないと、GitHub が定期実行を自動停止します。停止したらメールが届くので、Actions 画面から再度有効にしてください。

## カスタマイズ

各チャンネルの設定は `bot/channels/<チャンネル名>.py` にまとまっています。

| 変更したいこと | 場所 |
| --- | --- |
| ソースの追加・削除 | 各ファイルの `FEEDS` |
| 選定基準・要約のトーン・出力項目 | 各ファイルの `SYSTEM_PROMPT` / `SCHEMA` / `render` |
| 件数 | 各ファイルの `MAX_ITEMS` |
| 投稿時刻 | 各ファイルの `SLOTS` と `.github/workflows/<チャンネル名>.yml` の `cron` |
| チャンネルの追加 | `bot/channels/` にファイルを追加して `bot/channels/__init__.py` に登録し、ワークフローを追加 |

## ローカルで試す

```bash
pip install -r requirements.txt     # Claude Code（claude コマンド）にログイン済みであること
python -m bot pain-points --dry-run # Slack に投稿せず結果を表示
SLACK_WEBHOOK=https://hooks.slack.com/... python -m bot pain-points   # 実際に投稿
```
