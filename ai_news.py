"""AI 系ニュースを集めて、日本語要約 + リンク付きで Slack に投稿する（朝 8:00 / 夜 20:00）。

流れ:
  1. RSS フィードから直近 N 時間の記事を収集（過去に投稿済みの URL は除外）
  2. Claude に重要記事の選定と日本語要約を依頼（JSON で受け取る）
     直近に配信済みの話題も渡し、別メディアの同じニュースを繰り返さないようにする
  3. Slack Incoming Webhook に Block Kit で投稿し、投稿履歴を保存

環境変数:
  CLAUDE_CODE_OAUTH_TOKEN  `claude setup-token` で発行したトークン（Claude のサブスク枠で実行）
  SLACK_WEBHOOK_URL   Slack Incoming Webhook URL
  LOOKBACK_HOURS      収集対象の時間幅（既定 24）
  MAX_ITEMS           投稿する記事数（既定 8）
  EDITION             "morning" / "evening"。未指定なら現在時刻 (JST) から自動判定
  WAIT_FOR_SLOT       "1" なら、早く起動した場合に配信時刻（8:00 / 20:00）まで待ってから投稿
  HISTORY_FILE        投稿履歴の保存先（既定 posted_history.json）
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser
import requests

JST = timezone(timedelta(hours=9))

FEEDS: dict[str, str] = {
    "TechCrunch": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "VentureBeat": "https://venturebeat.com/category/ai/feed/",
    "MIT Technology Review": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "OpenAI": "https://openai.com/news/rss.xml",
    "Google AI": "https://blog.google/technology/ai/rss/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "Hacker News": "https://hnrss.org/newest?q=AI+OR+LLM+OR+GPT+OR+Claude+OR+Gemini&points=100",
    "ITmedia AI+": "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",
}

MODEL = "claude-opus-5-5"
HISTORY_DAYS = 7

EDITIONS = {
    "morning": {"label": ":sunrise: 朝のAIニュース", "post_at": "08:00"},
    "evening": {"label": ":city_sunset: 夜のAIニュース", "post_at": "20:00"},
}
USER_AGENT = "Mozilla/5.0 (compatible; ai-news-slack/1.0)"


@dataclass
class Article:
    id: int
    source: str
    title: str
    url: str
    published: datetime
    summary: str


def fetch_articles(lookback_hours: int, exclude_urls: set[str]) -> list[Article]:
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    articles: list[Article] = []
    seen_urls: set[str] = set(exclude_urls)

    for source, feed_url in FEEDS.items():
        try:
            resp = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as e:  # 1 フィードの失敗で全体を止めない
            print(f"[warn] {source}: {e}", file=sys.stderr)
            continue

        count = 0
        for entry in feed.entries:
            parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if not parsed:
                continue
            published = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            url = entry.get("link", "")
            if published < since or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            summary = entry.get("summary", "") or ""
            articles.append(
                Article(
                    id=len(articles),
                    source=source,
                    title=entry.get("title", "").strip(),
                    url=url,
                    published=published,
                    summary=_strip_html(summary)[:500],
                )
            )
            count += 1
        print(f"[info] {source}: {count} 件", file=sys.stderr)

    return articles


def load_history(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    return [h for h in history if datetime.fromisoformat(h["posted_at"]) >= cutoff]


def save_history(path: str, history: list[dict], digest: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    history = history + [
        {"url": item["url"], "title_ja": item["title_ja"], "posted_at": now}
        for item in digest["items"]
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)


def _strip_html(text: str) -> str:
    import re

    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


SYSTEM_PROMPT = """あなたは日本のビジネスパーソン・エンジニア向けに AI 業界ニュースをキュレーションする編集者です。
与えられた記事リストから、その日に読む価値が高いものを選び、日本語で簡潔に要約してください。

選定基準:
- 主要 AI 企業（OpenAI, Anthropic, Google, Meta, Microsoft, NVIDIA など）の新モデル・新製品・重要発表
- 資金調達・M&A・規制・訴訟など、ビジネスや市場に影響するニュース
- 開発者が実務で使える新ツール・OSS・研究成果
- 同じ出来事を扱う複数記事は 1 件にまとめ、最も情報量の多い記事の id を使う
- 宣伝色が強いだけの記事や、AI と関係の薄い記事は除外

要約のルール:
- title_ja: 日本語の見出し（40 字程度まで、固有名詞は原語のままで可）
- summary_ja: 何が起きたか + なぜ重要か を 2〜3 文で。誇張や推測は避け、記事にある事実ベースで書く
- category: "モデル/製品", "ビジネス", "規制/社会", "研究", "開発者向け" のいずれか
- headline: その日の全体像を 1 文で（60 字程度まで）"""

OUTPUT_SCHEMA = {
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
                    "category": {
                        "type": "string",
                        "enum": ["モデル/製品", "ビジネス", "規制/社会", "研究", "開発者向け"],
                    },
                },
                "required": ["id", "title_ja", "summary_ja", "category"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "items"],
    "additionalProperties": False,
}


def summarize(articles: list[Article], max_items: int, recent_titles: list[str]) -> dict:
    payload = [
        {
            "id": a.id,
            "source": a.source,
            "title": a.title,
            "published": a.published.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
            "summary": a.summary,
        }
        for a in articles
    ]
    user_content = (
        f"以下は直近の AI 関連記事 {len(articles)} 件です。"
        f"重要度の高い順に最大 {max_items} 件を選んで要約してください。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    if recent_titles:
        user_content += (
            "\n\n以下は直近の配信で既に紹介した話題です。同じ出来事を扱う記事は選ばないでください"
            "（続報で大きな新事実がある場合のみ可）。\n"
            + "\n".join(f"- {t}" for t in recent_titles)
        )

    # Claude Code CLI（claude -p）経由で呼ぶので、Claude のサブスクリプション枠で動く
    env = os.environ.copy()
    if token := env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        # ターミナルの折り返しごとコピーされた改行・空白を除去
        env["CLAUDE_CODE_OAUTH_TOKEN"] = "".join(token.split())
    proc = subprocess.run(
        [
            "claude", "-p",
            "--model", MODEL,
            "--system-prompt", SYSTEM_PROMPT,
            "--json-schema", json.dumps(OUTPUT_SCHEMA, ensure_ascii=False),
            "--output-format", "json",
            "--tools", "",  # ファイル操作やコマンド実行はさせない
            "--max-turns", "5",
        ],
        input=user_content,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude -p の実行に失敗しました: {proc.stderr or proc.stdout}") from None
    if output.get("is_error") or not output.get("structured_output"):
        raise RuntimeError(f"claude -p がエラーを返しました: {output.get('subtype')} {output.get('result')}")
    result = output["structured_output"]

    # Claude が返した id を元記事に紐付け（URL は必ず元フィードのものを使う）
    by_id = {a.id: a for a in articles}
    items = []
    for item in result["items"][:max_items]:
        article = by_id.get(item["id"])
        if article is None:
            continue
        items.append({**item, "url": article.url, "source": article.source})
    return {"headline": result["headline"], "items": items}


CATEGORY_EMOJI = {
    "モデル/製品": ":rocket:",
    "ビジネス": ":moneybag:",
    "規制/社会": ":classical_building:",
    "研究": ":microscope:",
    "開発者向け": ":hammer_and_wrench:",
}


def build_blocks(digest: dict, today: datetime, edition: str) -> list[dict]:
    weekday = "月火水木金土日"[today.weekday()]
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{EDITIONS[edition]['label']} {today:%-m/%-d}({weekday})",
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{_esc(digest['headline'])}*"}},
        {"type": "divider"},
    ]
    for i, item in enumerate(digest["items"], 1):
        emoji = CATEGORY_EMOJI.get(item["category"], ":small_blue_diamond:")
        text = (
            f"{emoji} *{i}. <{item['url']}|{_esc(item['title_ja'])}>*\n"
            f"{_esc(item['summary_ja'])}"
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}})
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"{item['category']} ・ {_esc(item['source'])}"}
                ],
            }
        )
    return blocks


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def post_to_slack(webhook_url: str, digest: dict, blocks: list[dict]) -> None:
    fallback_text = f"AIニュース: {digest['headline']}"
    resp = requests.post(
        webhook_url,
        json={"text": fallback_text, "blocks": blocks, "unfurl_links": False},
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Slack 投稿失敗: {resp.status_code} {resp.text}")


def detect_edition(now: datetime) -> str:
    return "morning" if 2 <= now.hour < 14 else "evening"


def wait_until(post_at: str) -> None:
    """起動が早すぎた場合、指定時刻 (JST) まで待つ。最大 60 分。"""
    hour, minute = map(int, post_at.split(":"))
    now = datetime.now(JST)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    wait = (target - now).total_seconds()
    if 0 < wait <= 3600:
        print(f"[info] {post_at} JST まで {int(wait)} 秒待機", file=sys.stderr)
        time.sleep(wait)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Slack に投稿せず標準出力に表示")
    args = parser.parse_args()

    lookback = int(os.environ.get("LOOKBACK_HOURS", "24"))
    max_items = int(os.environ.get("MAX_ITEMS", "8"))
    history_file = os.environ.get("HISTORY_FILE", "posted_history.json")
    edition = os.environ.get("EDITION") or detect_edition(datetime.now(JST))
    if edition not in EDITIONS:
        raise ValueError(f"EDITION は morning / evening のいずれか: {edition}")

    history = load_history(history_file)
    posted_urls = {h["url"] for h in history}
    print(f"[info] {edition} 版 / 投稿履歴 {len(history)} 件", file=sys.stderr)

    articles = fetch_articles(lookback, posted_urls)
    print(f"[info] 合計 {len(articles)} 件取得（投稿済みを除く）", file=sys.stderr)
    if not articles:
        print("[warn] 新しい記事が見つかりませんでした", file=sys.stderr)
        return 0

    # 直近 2 日分（約 4 回分）の配信タイトルを渡して、同じ話題の重複を避ける
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    recent_titles = [
        h["title_ja"] for h in history if datetime.fromisoformat(h["posted_at"]) >= recent_cutoff
    ]
    digest = summarize(articles, max_items, recent_titles)
    blocks = build_blocks(digest, datetime.now(JST), edition)

    if args.dry_run:
        print(json.dumps(digest, ensure_ascii=False, indent=2))
        return 0

    webhook_url = os.environ["SLACK_WEBHOOK_URL"]
    if os.environ.get("WAIT_FOR_SLOT") == "1":
        wait_until(EDITIONS[edition]["post_at"])
    post_to_slack(webhook_url, digest, blocks)
    save_history(history_file, history, digest)
    print("[info] Slack に投稿しました", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
