"""#pain-points: 「こういうのが欲しい」「これが面倒」という声から、需要のありそうな課題を拾う（毎日 12:00）。"""

from __future__ import annotations

import json

from bot.core import DIVIDER, Context, Item, Slot, context_block, esc, fetch_feeds, link, section

NAME = "pain-points"
WEBHOOK_ENV = "SLACK_WEBHOOK_PAIN_POINTS"
SLOTS = [Slot("daily", "12:00", ":mag: 今日の課題発見")]
HISTORY_DAYS = 14
MAX_ITEMS = 5
LOOKBACK_HOURS = 48

FEEDS = {
    # Reddit は連続アクセスで制限されるので、複数の subreddit を 1 リクエストにまとめる
    "Reddit": "https://www.reddit.com/r/SomebodyMakeThis+AppIdeas+smallbusiness+Entrepreneur+startups+freelance/top/.rss?t=day&limit=50",
    "Ask HN": "https://hnrss.org/ask?points=20",
    "はてブ（暮らし）": "https://b.hatena.ne.jp/hotentry/life.rss",
    "はてブ（経済）": "https://b.hatena.ne.jp/hotentry/economics.rss",
    "はてブ（IT）": "https://b.hatena.ne.jp/hotentry/it.rss",
}

SYSTEM_PROMPT = """あなたは、起業を目指すエンジニアが「解く価値のある課題」を見つけるのを手伝うリサーチャーです。
与えられた投稿（Reddit・Ask HN・はてなブックマークの人気記事など）から、人や事業者が困っていること・不満・
「こういうものが欲しい」という声を見つけ、事業の種になりそうな課題として日本語で整理してください。

選ぶもの:
- 具体的な困りごとがあり、同じ悩みを持つ人が他にも多そうなもの（コメントの多さ・共感の声などがあると良い）
- お金や時間を払ってでも解決したい度合いが高そうなもの（特に事業者の業務上の悩み）
- ソフトウェアやサービスで解決できる余地があるもの

選ばないもの:
- 個人的な愚痴で一般化できないもの、政治・事件・ゴシップ
- すでに定番の解決策が広く普及していて、不満も出ていないもの
- 同じ課題の重複

各項目のルール:
- pain: 課題を一言で（40 字程度まで）
- who: 誰が困っているか（職種・業種・状況をできるだけ具体的に）
- evidence: 投稿から読み取れる困りごとの中身と、共感・反響の様子（2 文程度）
- existing: 今ある解決策と、それでは足りない点（わからなければ推定と明記）
- opportunity: 事業にするならどんな形があり得るか（1〜2 文。小さく始める方法があれば添える）
- strength: 需要の強さの目安。"強"（業務上の損失や支払い意思がはっきりしている）/ "中" / "弱"（面白いが支払い意思は不明）
- headline: 今日の課題全体から言える傾向を 1 文（60 字程度まで）"""

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
                    "pain": {"type": "string"},
                    "who": {"type": "string"},
                    "evidence": {"type": "string"},
                    "existing": {"type": "string"},
                    "opportunity": {"type": "string"},
                    "strength": {"type": "string", "enum": ["強", "中", "弱"]},
                },
                "required": ["id", "pain", "who", "evidence", "existing", "opportunity", "strength"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "items"],
    "additionalProperties": False,
}

STRENGTH_EMOJI = {"強": ":fire:", "中": ":thermometer:", "弱": ":seedling:"}


def collect(ctx: Context) -> list[Item]:
    return fetch_feeds(FEEDS, LOOKBACK_HOURS, ctx.seen_urls, summary_chars=800)


def build_prompt(items: list[Item], ctx: Context) -> str:
    text = (
        f"以下は直近の投稿 {len(items)} 件です。事業の種として有望な課題を最大 {MAX_ITEMS} 件、有望な順に選んでください。"
        "条件に合うものが少なければ、件数が少なくてもかまいません。\n\n"
        + json.dumps([i.to_prompt() for i in items], ensure_ascii=False)
    )
    if recent := ctx.recent_titles(days=14):
        text += "\n\n以下は過去 2 週間に紹介済みの課題です。ほぼ同じ課題は選ばないでください。\n" + "\n".join(
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
    blocks = [ctx.header(), section(f"*{esc(digest['headline'])}*"), DIVIDER]
    for i, (d, item) in enumerate(picked, 1):
        text = (
            f"*{i}. {link(item.url, d['pain'])}*\n"
            f":bust_in_silhouette: *誰が:* {esc(d['who'])}\n"
            f":speech_balloon: *声:* {esc(d['evidence'])}\n"
            f":toolbox: *既存の解決策:* {esc(d['existing'])}\n"
            f":bulb: *事業にするなら:* {esc(d['opportunity'])}"
        )
        blocks.append(section(text))
        emoji = STRENGTH_EMOJI.get(d["strength"], "")
        blocks.append(context_block(f"{emoji} 需要の強さ: {d['strength']} ・ {esc(item.source)}"))
    return blocks


def history_entries(digest: dict, ctx: Context) -> list[dict]:
    return [{"url": item.url, "title": d["pain"]} for d, item in _picked(digest, ctx)]
