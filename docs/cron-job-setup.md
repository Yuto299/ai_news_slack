# cron-job.org で配信時刻どおりに動かす手順

GitHub Actions の定期実行（cron）は、混雑していると数時間遅れることがあります。
そこで無料の [cron-job.org](https://cron-job.org/) から、配信の 10 分前に GitHub へ「今すぐ実行して」と指示を送ります。
起動したワークフローは配信時刻まで待ってから投稿するので、ほぼ時刻どおりに届きます。

- 費用: cron-job.org も GitHub も無料
- GitHub 側の定期実行は予備として残しています。cron-job.org の回で投稿済みなら、遅れて来た GitHub の回は何もしません（二重投稿なし）
- 所要時間: 10 分ほど

---

## 1. GitHub のトークンを作る（3 分）

cron-job.org が GitHub に指示を送るための鍵です。このリポジトリの Actions を動かす権限だけに絞ります。

1. https://github.com/settings/personal-access-tokens/new を開く
   （GitHub 右上のアイコン → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token でも同じ）
2. 次のように入力する

   | 項目 | 入力 |
   | --- | --- |
   | Token name | `cron-job.org news_slack` |
   | Expiration | `Custom` で 1 年後の日付（期限が近づくと GitHub からメールが届きます） |
   | Repository access | **Only select repositories** → `Yuto299/news_slack` |
   | Permissions → Repository permissions | **Actions** を **Read and write** にする（他は触らない。Metadata: Read-only は自動で付きます） |

3. 一番下の **Generate token** を押し、表示された `github_pat_...` をコピーする
   （この画面を閉じると二度と表示されないので、次の手順が終わるまでメモ帳などに貼っておく）

> トークンはチャットやコードには貼らず、cron-job.org にだけ入力してください。

---

## 2. cron-job.org に登録する（7 分）

### 2-1. アカウント作成

1. https://console.cron-job.org/signup で無料登録（メール認証あり）
2. ログイン後、右上のアカウントメニュー → **Settings** で **Timezone** を `Asia/Tokyo` にして保存

### 2-2. ジョブを 7 個作る

**CREATE CRONJOB** を押し、下の「共通設定」と「ジョブ一覧」の内容で 1 個ずつ作ります。
1 個目を作ったら、一覧のメニューから **Clone** して 2 個目以降を作ると早いです（変えるのはタイトル・URL・時刻・Request body だけ）。

#### 共通設定

**COMMON** タブ

| 項目 | 入力 |
| --- | --- |
| Title | ジョブ一覧の「タイトル」 |
| URL | ジョブ一覧の「URL」 |
| Execution schedule | **Custom** を選び、ジョブ一覧の「時刻」「曜日」を設定（Days of month: 毎日 / Months: 毎月） |

**ADVANCED** タブ

| 項目 | 入力 |
| --- | --- |
| Time zone | `Asia/Tokyo` |
| Request method | `POST` |
| Headers（Add で 4 つ追加） | `Accept` = `application/vnd.github+json`<br>`Authorization` = `Bearer github_pat_...`（手順 1 のトークン。`Bearer` と半角スペースを付ける）<br>`X-GitHub-Api-Version` = `2022-11-28`<br>`Content-Type` = `application/json` |
| Request body | ジョブ一覧の「Request body」をそのまま貼る |
| Notify me when... the job fails | ON にしておくと、失敗したときにメールが届きます |

#### ジョブ一覧

URL はすべて `https://api.github.com/repos/Yuto299/news_slack/actions/workflows/<ファイル名>/dispatches` の形です。

| タイトル | 時刻 | 曜日 | URL の `<ファイル名>` | Request body |
| --- | --- | --- | --- | --- |
| ai-news 朝 | 07:50 | 毎日 | `daily-ai-news.yml` | `{"ref":"main","inputs":{"slot":"morning","scheduled":"true"}}` |
| ai-news 夜 | 19:50 | 毎日 | `daily-ai-news.yml` | `{"ref":"main","inputs":{"slot":"evening","scheduled":"true"}}` |
| pain-points | 11:50 | 毎日 | `pain-points.yml` | `{"ref":"main","inputs":{"scheduled":"true"}}` |
| launches | 17:50 | 毎日 | `launches.yml` | `{"ref":"main","inputs":{"scheduled":"true"}}` |
| small-biz | 20:50 | 毎日 | `small-biz.yml` | `{"ref":"main","inputs":{"scheduled":"true"}}` |
| weekly-trends | 09:50 | 日曜のみ | `weekly-trends.yml` | `{"ref":"main","inputs":{"scheduled":"true"}}` |
| subsidies | 08:50 | 月曜・木曜のみ | `subsidies.yml` | `{"ref":"main","inputs":{"scheduled":"true"}}` |

例: ai-news 朝の URL は `https://api.github.com/repos/Yuto299/news_slack/actions/workflows/daily-ai-news.yml/dispatches`

---

## 3. 動作確認

1. cron-job.org でジョブを 1 つ開き、**TEST RUN** → **START TEST RUN** を押す
2. 結果が **204 No Content** なら成功
3. GitHub の [Actions](https://github.com/Yuto299/news_slack/actions) に、そのワークフローの実行が増えていることを確認する

配信時刻の前後（30 分前〜90 分後）以外にテストすれば、ワークフローは「配信枠に当たらない起動のためスキップ」で何も投稿せずに終わります（Slack に余計な投稿は出ません）。

### うまくいかないとき

| cron-job.org の結果 | 原因と対処 |
| --- | --- |
| `401 Unauthorized` | トークンが間違っているか期限切れ。`Authorization` が `Bearer ` + トークンになっているか確認 |
| `403` / `404 Not Found` | トークンの権限不足（Actions: Read and write になっているか、対象リポジトリが `news_slack` か）、または URL のファイル名の間違い |
| `422 Unprocessable Entity` | Request body の間違い（コピペ時に引用符が全角になっていないかなど） |

---

## トークンの期限が切れたら

1. 手順 1 と同じ画面でトークンを作り直す（または既存トークンの **Regenerate token**）
2. cron-job.org の 7 個のジョブの `Authorization` ヘッダーを新しいトークンに差し替える

期限切れのあいだも、GitHub 側の定期実行（予備）が遅れながら動くので、配信が完全に止まることはありません。
