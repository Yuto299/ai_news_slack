"""#subsidies: 起業・小規模事業向けの補助金・助成金・支援制度の新着（毎週 月・木 9:00）。

jGrants（デジタル庁の補助金電子申請システム）の公開 API と、ミラサポplus（中小企業庁）のお知らせから集める。
一度確認した制度は、紹介しなかったものも含めて次回以降の候補から外す（MARK_ALL_SEEN）。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

from bot.core import JST, DIVIDER, Context, Item, Slot, context_block, esc, fetch_feeds, http_get, link, section

NAME = "subsidies"
WEBHOOK_ENV = "SLACK_WEBHOOK_SUBSIDIES"
SLOTS = [Slot("biweekly", "09:00", ":classical_building: 補助金・支援制度の新着", weekdays=(0, 3))]
HISTORY_DAYS = 400  # 募集期間が長い制度もあるので、確認済みの制度を長めに覚えておく
MARK_ALL_SEEN = True
MAX_ITEMS = 6

JGRANTS_API = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies"
# 対象地域: 全国対象の制度に加えて、ここに挙げた都県の制度だけを候補にする
TARGET_PREFECTURES = ["東京都", "神奈川県", "埼玉県", "千葉県", "山梨県"]
JGRANTS_KEYWORDS = ["創業", "起業", "スタートアップ", "新事業", "IT", "DX", "デジタル", "AI", "小規模", "販路開拓"]
FEEDS = {"ミラサポplus": "https://mirasapo-plus.go.jp/feed/"}

SYSTEM_PROMPT = """あなたは、会社員エンジニアが AI・IT 領域で起業する準備を手伝う、中小企業支援の専門家です。
与えられた補助金・助成金・支援制度の新着情報から、これから起業する人や創業間もない小さな会社が使えそうなものを選び、日本語で紹介してください。

選ぶ基準:
- 創業・起業・新事業・IT/DX/AI 導入・販路開拓・人材など、小規模なスタートアップが対象になり得るもの
- 読者の拠点は一都三県（東京都・神奈川県・埼玉県・千葉県）と山梨県。候補は全国対象か、これらの都県が対象のものに絞ってある
- 全国対象と都県の制度を同じ基準で比べ、使える可能性が高いものを選ぶ。市区町村の制度は、その市区町村に拠点を置く必要があることを fit で明記する
- 特定業種（農業・漁業・医療機関・運輸など）や特定設備に限られるもの、個人の生活支援、奨学金返還支援は除く
- 募集締切が過ぎたもの・締切まで 3 日以内のものは除く

各項目のルール:
- name: 制度名（長ければ要点がわかる形に短くしてよい）
- who: 対象者（地域・事業規模・創業前後などの条件）
- amount: 補助上限額・補助率（わかる範囲で。不明なら「要確認」）
- deadline: 募集締切（YYYY-MM-DD。不明なら「要確認」）
- fit: 起業準備中のエンジニアにとってどう使えそうか、注意点は何か（1〜2 文）
- headline: 今回の新着全体の要点を 1 文（60 字程度まで）。該当がなければ「今回は対象になりそうな新着はありませんでした」

該当するものがなければ items は空にしてください。"""

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
                    "who": {"type": "string"},
                    "amount": {"type": "string"},
                    "deadline": {"type": "string"},
                    "fit": {"type": "string"},
                },
                "required": ["id", "name", "who", "amount", "deadline", "fit"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "items"],
    "additionalProperties": False,
}


def _fetch_jgrants(seen: set[str]) -> list[Item]:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    found: dict[str, Item] = {}
    for kw in JGRANTS_KEYWORDS:
        try:
            resp = http_get(
                JGRANTS_API,
                params={"keyword": kw, "sort": "created_date", "order": "DESC", "acceptance": "1"},
            )
            results = resp.json().get("result", [])
        except Exception as e:
            print(f"[warn] jGrants[{kw}]: {e}", file=sys.stderr)
            continue
        for r in results:
            url = f"https://www.jgrants-portal.go.jp/subsidy/{r['id']}"
            deadline = (r.get("acceptance_end_datetime") or "")[:10]
            area = r.get("target_area_search") or ""
            if url in seen or url in found or (deadline and deadline < today):
                continue
            if area and "全国" not in area and not any(p in area for p in TARGET_PREFECTURES):
                continue
            found[url] = Item(
                id=0,
                source="jGrants",
                title=r.get("title", ""),
                url=url,
                extra={
                    "deadline": deadline,
                    "max_amount_yen": r.get("subsidy_max_limit"),
                    "area": area,
                    "employees": r.get("target_number_of_employees", ""),
                },
            )
    print(f"[info] jGrants: {len(found)} 件", file=sys.stderr)
    return list(found.values())


def collect(ctx: Context) -> list[Item]:
    items = _fetch_jgrants(ctx.seen_urls)
    items += fetch_feeds(FEEDS, 24 * 7, ctx.seen_urls, summary_chars=300)
    for n, item in enumerate(items):
        item.id = n
    return items


def build_prompt(items: list[Item], ctx: Context) -> str:
    return (
        f"今日は {ctx.now:%Y-%m-%d} です。以下は新着の補助金・支援制度の情報 {len(items)} 件です。"
        f"条件に合うものを最大 {MAX_ITEMS} 件、おすすめ順に選んでください。\n\n"
        + json.dumps([i.to_prompt() for i in items], ensure_ascii=False)
    )


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
            f":busts_in_silhouette: *対象:* {esc(d['who'])}\n"
            f":yen: *金額:* {esc(d['amount'])}\n"
            f":alarm_clock: *締切:* {esc(d['deadline'])}\n"
            f":bulb: {esc(d['fit'])}"
        )
        blocks.append(section(text))
        blocks.append(context_block(esc(item.source)))
    blocks.append(context_block("申請前に、必ず公式ページで最新の要件と締切を確認してください。"))
    return blocks


def history_entries(digest: dict, ctx: Context) -> list[dict]:
    return [{"url": item.url, "title": d["name"]} for d, item in _picked(digest, ctx)]
