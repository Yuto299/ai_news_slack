"""#small-biz: 個人・少人数で再現できそうなスモールビジネス事例（毎日 21:00）。

SNS で見かけるような「〇〇で月 xx 万円」系の話を、真偽の目安付きで整理する。
"""

from __future__ import annotations

import json

from bot.core import DIVIDER, Context, Item, Slot, context_block, esc, fetch_feeds, link, section

NAME = "small-biz"
WEBHOOK_ENV = "SLACK_WEBHOOK_SMALL_BIZ"
SLOTS = [Slot("daily", "21:00", ":seedling: スモールビジネス案")]
HISTORY_DAYS = 14
MAX_ITEMS = 5
LOOKBACK_HOURS = 48

FEEDS = {
    # Reddit は連続アクセスで制限されるので、複数の subreddit を 1 リクエストにまとめる
    "Reddit": "https://www.reddit.com/r/SideProject+microsaas+indiehackers+EntrepreneurRideAlong+sweatystartup/top/.rss?t=day&limit=50",
    "Hacker News": "https://hnrss.org/newest?q=MRR+OR+revenue+OR+%22side+project%22+OR+bootstrapped&points=30",
    "Zenn": "https://zenn.dev/topics/個人開発/feed",
    "Qiita": "https://qiita.com/tags/個人開発/feed",
    "note（副業）": "https://note.com/hashtag/副業/rss",
    "note（個人開発）": "https://note.com/hashtag/個人開発/rss",
}

SYSTEM_PROMPT = """あなたは、会社員エンジニアが個人や少人数で始められるスモールビジネスを探すのを手伝うリサーチャーです。
与えられた投稿（Reddit・Hacker News・Zenn・Qiita・note など）から、実際に収益が出ている、または出そうな事業の事例を選び、日本語で整理してください。

選ぶもの:
- 具体的なプロダクト・サービス・販売方法があり、売上・利用者数・手順などの中身がある事例
- 個人〜数人で、初期費用が小さく（目安 100 万円以下）、他の人が再現できそうなもの
- SaaS・アプリ・テンプレート販売・受託の型・ローカルビジネス・コンテンツ販売など形態は問わない（AI 活用でなくてもよい）

選ばないもの:
- 中身のない自慢、情報商材や高額スクールへの誘導、ギャンブル・投機、マルチ商法
- 単なる技術記事や日記で、事業としての要素（誰に何を売るか）がないもの
- 同じ事例の重複

各項目のルール:
- name: 事業を一言で（30 字程度まで）
- what: 誰に何をどう売っているか（1〜2 文）
- revenue_claim: 主張されている売上・利益・利用者数など（数字があれば原文どおり、通貨も明記。なければ「記載なし」）
- how_to: 再現するための手順を 2〜4 ステップで（「1. 〜 2. 〜」の形で 1 つの文字列に）
- cost: 初期費用・必要時間・必要スキルの目安（記載から読み取れる範囲で。推測なら「推定」と書く）
- credibility: 投稿の信頼度。"証拠あり"（売上画面・決済データ・公開ダッシュボードなどの裏付けに触れている）/ "自己申告"（数字はあるが裏付けなし）/ "要注意"（誇張・勧誘の気配、数字が不自然）
- credibility_reason: その判断の理由を 1 文
- japan_fit: 日本で同じことをやる場合の注意点や工夫を 1 文
- headline: 今日の事例全体から言える傾向を 1 文（60 字程度まで）

真偽を確かめる手段はないので、投稿に書かれていることを事実のように断定せず、「〜と主張」「〜とのこと」のように書いてください。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "what": {"type": "string"},
                    "revenue_claim": {"type": "string"},
                    "how_to": {"type": "string"},
                    "cost": {"type": "string"},
                    "credibility": {"type": "string", "enum": ["証拠あり", "自己申告", "要注意"]},
                    "credibility_reason": {"type": "string"},
                    "japan_fit": {"type": "string"},
                },
                "required": [
                    "id", "name", "what", "revenue_claim", "how_to", "cost",
                    "credibility", "credibility_reason", "japan_fit",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "items"],
    "additionalProperties": False,
}

CREDIBILITY_EMOJI = {"証拠あり": ":large_green_circle:", "自己申告": ":large_yellow_circle:", "要注意": ":red_circle:"}


def collect(ctx: Context) -> list[Item]:
    return fetch_feeds(FEEDS, LOOKBACK_HOURS, ctx.seen_urls, summary_chars=800)


def build_prompt(items: list[Item], ctx: Context) -> str:
    text = (
        f"以下は直近の投稿 {len(items)} 件です。最大 {MAX_ITEMS} 件の事例を、再現しやすく参考になる順に選んでください。"
        "条件に合うものが少なければ、件数が少なくてもかまいません。\n\n"
        + json.dumps([i.to_prompt() for i in items], ensure_ascii=False)
    )
    if recent := ctx.recent_titles(days=14):
        text += "\n\n以下は過去 2 週間に紹介済みの事例です。同じ事例は選ばないでください。\n" + "\n".join(
            f"- {t}" for t in recent
        )
    return text


def _picked(digest: dict, ctx: Context) -> list[tuple[dict, Item]]:
    by_id = ctx.items_by_id
    return [(d, by_id[d["id"]]) for d in digest["items"][:MAX_ITEMS] if d["id"] in by_id]


def render(digest: dict, ctx: Context) -> list[dict]:
    picked = _picked(digest, ctx)
    if not picked:
        return []
    blocks = [
        ctx.header(),
        section(f"*{esc(digest['headline'])}*"),
        context_block("投稿者の主張をまとめたものです。売上などの数字は検証されていません。"),
        DIVIDER,
    ]
    for i, (d, item) in enumerate(picked, 1):
        emoji = CREDIBILITY_EMOJI.get(d["credibility"], ":white_circle:")
        text = (
            f"*{i}. {link(item.url, d['name'])}*\n"
            f"{esc(d['what'])}\n"
            f":chart_with_upwards_trend: *売上など:* {esc(d['revenue_claim'])}\n"
            f":footprints: *再現手順:* {esc(d['how_to'])}\n"
            f":moneybag: *初期費用・時間:* {esc(d['cost'])}\n"
            f":jp: *日本でやるなら:* {esc(d['japan_fit'])}"
        )
        blocks.append(section(text))
        blocks.append(
            context_block(f"{emoji} 信頼度: {d['credibility']}（{esc(d['credibility_reason'])}） ・ {esc(item.source)}")
        )
    return blocks


def history_entries(digest: dict, ctx: Context) -> list[dict]:
    return [{"url": item.url, "title": d["name"]} for d, item in _picked(digest, ctx)]
