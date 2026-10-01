"""#ai-news: AI 業界ニュース（朝 8:00 / 夜 20:00）。"""

from __future__ import annotations

import json

from bot.core import DIVIDER, Context, Item, Slot, context_block, esc, fetch_feeds, item_text, section

NAME = "ai-news"
WEBHOOK_ENV = "SLACK_WEBHOOK_URL"
SLOTS = [
    Slot("morning", "08:00", "朝のAIニュース"),
    Slot("evening", "20:00", "夜のAIニュース"),
]
HISTORY_DAYS = 8  # 週次まとめ（#weekly-trends）が 1 週間分を読むため少し長めに残す
MAX_ITEMS = 10
LOOKBACK_HOURS = 24

FEEDS = {
    "TechCrunch": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "The Decoder": "https://the-decoder.com/feed/",
    "Ars Technica": "https://arstechnica.com/ai/feed/",
    "MIT Technology Review": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "OpenAI": "https://openai.com/news/rss.xml",
    "Google AI": "https://blog.google/technology/ai/rss/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "Hacker News": "https://hnrss.org/newest?q=AI+OR+LLM+OR+GPT+OR+Claude+OR+Gemini&points=100",
    # 国内（AI 以外の記事も含むので、選定時に AI 関連に絞る）
    "ITmedia AI+": "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",
    "ITmedia エンタープライズ": "https://rss.itmedia.co.jp/rss/2.0/enterprise.xml",
    "日経xTECH": "https://xtech.nikkei.com/rss/index.rdf",
    "Publickey": "https://www.publickey1.jp/atom.xml",
    # ビジネス・スタートアップ系（AI 以外の記事も含むので、選定時に AI 関連に絞る）
    "TechCrunch Startups": "https://techcrunch.com/category/startups/feed/",
    "TechCrunch Venture": "https://techcrunch.com/category/venture/feed/",
    "Crunchbase News": "https://news.crunchbase.com/feed/",
}

SYSTEM_PROMPT = """あなたは AI 領域での起業を目指すエンジニア向けに、AI 業界ニュースをキュレーションする編集者です。
与えられた記事リストから、その日に読む価値が高いものを選び、日本語で簡潔に要約してください。
読者は技術がわかる前提で、事業機会・市場の動き・ビジネスモデルに特に関心があります。

選定基準:
- 主要 AI 企業（OpenAI, Anthropic, Google, Meta, Microsoft, NVIDIA など）の新モデル・新製品・重要発表
- AI スタートアップの資金調達・M&A・急成長事例・新しいビジネスモデル・価格改定・大企業の導入事例
- 規制・訴訟など、事業環境に影響するニュース
- 開発者が実務で使える新ツール・OSS・研究成果
- 同じ出来事を扱う複数記事は 1 件にまとめ、最も情報量の多い記事の id を使う
- 宣伝色が強いだけの記事や、AI と関係の薄い記事は除外（スタートアップ系・国内 IT 系メディアには AI 以外の記事も多く含まれる）

件数が 10 件のときのジャンル配分の目安（その日のニュース次第で ±1〜2 件は調整してよい。質の低い記事で枠を埋めない）:
- ビジネス: 4 件（資金調達・M&A・新サービス・導入事例・市場動向）
- モデル/製品: 3 件
- 開発者向け / 研究: 2 件
- 規制/社会: 1 件
- 上記とは別軸で、10 件のうち 2 件程度は国内（日本）の話題を入れる（国内企業の AI 導入・国内スタートアップ・国内の規制動向など）

要約のルール:
- title_ja: 日本語の見出し（40 字程度まで、固有名詞は原語のままで可）
- summary_ja: 何が起きたか + なぜ重要か を 2〜3 文で。金額・評価額・顧客数など具体的な数字があれば入れる。誇張や推測は避け、記事にある事実ベースで書く
- insight_ja: 起業家の視点での示唆を 1 文（例: 空いている市場、真似できる戦略、参入障壁の変化）。記事から自然に言えることがなければ空文字
- category: "モデル/製品", "ビジネス", "規制/社会", "研究", "開発者向け" のいずれか
- headline: その日の全体像を 1 文で（60 字程度まで）"""

CATEGORIES = ["モデル/製品", "ビジネス", "規制/社会", "研究", "開発者向け"]
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
                    "title_ja": {"type": "string"},
                    "summary_ja": {"type": "string"},
                    "insight_ja": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                },
                "required": ["id", "title_ja", "summary_ja", "insight_ja", "category"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "items"],
    "additionalProperties": False,
}

def collect(ctx: Context) -> list[Item]:
    return fetch_feeds(FEEDS, LOOKBACK_HOURS, ctx.seen_urls)


def build_prompt(items: list[Item], ctx: Context) -> str:
    text = (
        f"以下は直近の AI 関連記事 {len(items)} 件です。"
        f"重要度の高い順に最大 {MAX_ITEMS} 件を選んで要約してください。\n\n"
        + json.dumps([i.to_prompt() for i in items], ensure_ascii=False)
    )
    # 直近 2 日分（約 4 回分）の配信タイトルを渡して、同じ話題の重複を避ける
    if recent := ctx.recent_titles(days=2):
        text += (
            "\n\n以下は直近の配信で既に紹介した話題です。同じ出来事を扱う記事は選ばないでください"
            "（続報で大きな新事実がある場合のみ可）。\n" + "\n".join(f"- {t}" for t in recent)
        )
    return text


def _picked(digest: dict, ctx: Context) -> list[tuple[dict, Item]]:
    by_id = ctx.items_by_id
    return [(d, by_id[d["id"]]) for d in digest["items"][:MAX_ITEMS] if d["id"] in by_id]


def render(digest: dict, ctx: Context) -> list[dict]:
    picked = _picked(digest, ctx)
    if not picked:
        return []
    blocks = [ctx.header(), section(esc(digest["headline"])), DIVIDER]
    for i, (d, item) in enumerate(picked, 1):
        blocks.append(section(item_text(i, item.url, d["title_ja"], d["summary_ja"], [("起業の視点", d["insight_ja"])])))
        blocks.append(context_block(f"{d['category']}　|　{esc(item.source)}"))
    return blocks


def history_entries(digest: dict, ctx: Context) -> list[dict]:
    # summary / category は #weekly-trends が 1 週間の振り返りに使う
    return [
        {"url": item.url, "title": d["title_ja"], "summary": d["summary_ja"], "category": d["category"]}
        for d, item in _picked(digest, ctx)
    ]
