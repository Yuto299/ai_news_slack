"""#weekly-trends: 1 週間の AI ニュースと資金調達から「お金と注目がどこに流れたか」をまとめる（毎週日曜 10:00）。

素材は #ai-news が 1 週間に配信した記事（履歴に要約付きで残っている）と、資金調達系フィードの 1 週間分。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

from bot.core import DIVIDER, Context, Item, Slot, context_block, esc, fetch_feeds, link, load_history, section

NAME = "weekly-trends"
WEBHOOK_ENV = "SLACK_WEBHOOK_WEEKLY_TRENDS"
SLOTS = [Slot("weekly", "10:00", ":bar_chart: 今週のAI・スタートアップ動向", weekdays=(6,))]
HISTORY_DAYS = 60

FEEDS = {
    "TechCrunch Venture": "https://techcrunch.com/category/venture/feed/",
    "TechCrunch Startups": "https://techcrunch.com/category/startups/feed/",
    "Crunchbase News": "https://news.crunchbase.com/feed/",
    "The Decoder": "https://the-decoder.com/feed/",
}

SYSTEM_PROMPT = """あなたは、AI 領域での起業を目指すエンジニアに毎週ブリーフィングをする投資アナリストです。
与えられた 1 週間分のニュース（AI ニュースとして配信済みの記事の要約と、資金調達・スタートアップ系の記事）を読み、
個別のニュースの羅列ではなく「今週、お金と注目がどこに流れたか」がわかるように、テーマ単位で日本語でまとめてください。

ルール:
- headline: 今週を一言で表すと（60 字程度まで）
- themes: 今週の主要テーマを 3〜5 個。重要な順に
  - title: テーマ名（30 字程度まで）
  - what_happened: 何が起きたか。具体的な企業名・金額・数字を入れて 2〜3 文
  - signal: そこから読み取れる市場の動き（どの領域に資金・人・顧客が向かっているか）を 1〜2 文
  - implication: 起業家として、参入・回避・様子見のどれが良さそうか、その理由を 1〜2 文
  - ids: 根拠となる記事の id を 1〜3 個
- next_week: 来週以降に注目しておくべきこと（予定されている発表・決算・規制の動き・続報待ちの案件など）を 2〜4 個。素材から言えることだけを書き、推測で予定を作らない
- 記事にない事実を足さない。数字は素材にあるものだけを使う"""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "what_happened": {"type": "string"},
                    "signal": {"type": "string"},
                    "implication": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "what_happened", "signal", "implication", "ids"],
                "additionalProperties": False,
            },
        },
        "next_week": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "themes", "next_week"],
    "additionalProperties": False,
}


def collect(ctx: Context) -> list[Item]:
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    ai_news = load_history(os.environ.get("AI_NEWS_HISTORY_FILE", "posted_history.json"), days=8)
    items = [
        Item(
            id=n,
            source=f"AIニュース（{h.get('category', '')}）",
            title=h["title"],
            url=h["url"],
            summary=h.get("summary", ""),
        )
        for n, h in enumerate(
            h for h in ai_news
            if not h.get("seen_only") and h["url"].startswith("http")
            and datetime.fromisoformat(h["posted_at"]) >= week_ago
        )
    ]
    print(f"[info] AIニュースの配信履歴: {len(items)} 件", file=sys.stderr)
    seen = {i.url for i in items}
    items += fetch_feeds(FEEDS, 24 * 7, seen, summary_chars=300, start_id=len(items))
    return items


def build_prompt(items: list[Item], ctx: Context) -> str:
    return (
        f"以下は今週 1 週間分の記事 {len(items)} 件です。\n\n"
        + json.dumps([i.to_prompt() for i in items], ensure_ascii=False)
    )


def render(digest: dict, ctx: Context) -> list[dict]:
    if not digest["themes"]:
        return []
    by_id = ctx.items_by_id
    blocks = [ctx.header(), section(f"*{esc(digest['headline'])}*"), DIVIDER]
    for i, t in enumerate(digest["themes"], 1):
        text = (
            f"*{i}. {esc(t['title'])}*\n"
            f"{esc(t['what_happened'])}\n"
            f":money_with_wings: *市場の動き:* {esc(t['signal'])}\n"
            f":compass: *起業家として:* {esc(t['implication'])}"
        )
        blocks.append(section(text))
        refs = [by_id[n] for n in t["ids"][:3] if n in by_id]
        if refs:
            blocks.append(context_block(" ・ ".join(link(r.url, r.title[:40]) for r in refs)))
    if digest["next_week"]:
        blocks.append(DIVIDER)
        blocks.append(section("*:calendar: 来週の注目*\n" + "\n".join(f"• {esc(x)}" for x in digest["next_week"])))
    return blocks


def history_entries(digest: dict, ctx: Context) -> list[dict]:
    return [{"url": f"weekly:{ctx.now:%Y-%m-%d}", "title": digest["headline"]}]

