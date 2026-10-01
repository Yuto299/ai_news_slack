"""#launches: Product Hunt / Show HN などの新プロダクトから、注目すべきものを拾う（毎日 18:00）。"""

from __future__ import annotations

import json

from bot.core import DIVIDER, Context, Item, Slot, context_block, esc, fetch_feeds, link, section

NAME = "launches"
WEBHOOK_ENV = "SLACK_WEBHOOK_LAUNCHES"
SLOTS = [Slot("daily", "18:00", ":rocket: 今日の新プロダクト")]
HISTORY_DAYS = 14
MAX_ITEMS = 6
LOOKBACK_HOURS = 30

FEEDS = {
    "Product Hunt": "https://www.producthunt.com/feed",
    "Show HN": "https://hnrss.org/show?points=50",
    "Launch HN": "https://hnrss.org/launches",
}

SYSTEM_PROMPT = """あなたは、起業を目指すエンジニア向けに新しいプロダクトをウォッチしているアナリストです。
与えられたローンチ情報（Product Hunt・Show HN・Launch HN）から、事業づくりの参考になるものを選び、日本語で紹介してください。

選ぶ基準:
- 新しい市場や切り口を開いている、伸びている兆しがある（反響・投票・コメントが多い）、ビジネスモデルが参考になる
- 小さなチームでも作れそうな規模のもの、または大きな流れ（AI エージェント化など）を象徴するもの
- 単なる技術デモ・趣味のゲーム・既存サービスの焼き直しにすぎないものは優先度を下げる
- 同じプロダクトの重複は 1 件にまとめる

各項目のルール:
- name: プロダクト名（原語のまま）
- what: 何をするプロダクトか、誰向けか（1〜2 文）
- why_notable: なぜ注目か（新しさ・反響・市場の空白など。1〜2 文）
- business_model: 収益モデル（読み取れる範囲で。不明なら推定と明記）
- takeaway: 起業家として盗めるポイントや、日本で応用するなら（1 文）
- headline: 今日のローンチ全体から言える傾向を 1 文（60 字程度まで）"""

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
                    "why_notable": {"type": "string"},
                    "business_model": {"type": "string"},
                    "takeaway": {"type": "string"},
                },
                "required": ["id", "name", "what", "why_notable", "business_model", "takeaway"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "items"],
    "additionalProperties": False,
}


def collect(ctx: Context) -> list[Item]:
    return fetch_feeds(FEEDS, LOOKBACK_HOURS, ctx.seen_urls, summary_chars=400)


def build_prompt(items: list[Item], ctx: Context) -> str:
    text = (
        f"以下は直近のローンチ {len(items)} 件です。最大 {MAX_ITEMS} 件を、注目度の高い順に選んでください。\n\n"
        + json.dumps([i.to_prompt() for i in items], ensure_ascii=False)
    )
    if recent := ctx.recent_titles(days=14):
        text += "\n\n以下は過去 2 週間に紹介済みのプロダクトです。同じものは選ばないでください。\n" + "\n".join(
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
            f"*{i}. {link(item.url, d['name'])}*\n"
            f"{esc(d['what'])}\n"
            f":eyes: *注目点:* {esc(d['why_notable'])}\n"
            f":moneybag: *収益モデル:* {esc(d['business_model'])}\n"
            f":bulb: _{esc(d['takeaway'])}_"
        )
        blocks.append(section(text))
        blocks.append(context_block(esc(item.source)))
    return blocks


def history_entries(digest: dict, ctx: Context) -> list[dict]:
    return [{"url": item.url, "title": d["name"]} for d, item in _picked(digest, ctx)]
