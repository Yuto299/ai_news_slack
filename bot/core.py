"""全チャンネル共通の処理: 収集 → Claude で選定・要約 → Slack 投稿 → 履歴保存。

各チャンネル（bot/channels/*.py）は以下を定義する:
  NAME            チャンネル名（コマンド引数・履歴のキー）
  WEBHOOK_ENV     Webhook URL を読む環境変数名（ワークフロー側で SLACK_WEBHOOK に入れて渡す）
  SLOTS           配信枠 [Slot(...)]。定期実行時は起動時刻から枠を判定する
  SYSTEM_PROMPT   Claude への指示
  SCHEMA          Claude の出力 JSON Schema
  collect(ctx)    候補の Item リストを返す
  build_prompt(items, ctx)    Claude に渡すユーザーメッセージ
  render(digest, ctx)         Slack Block Kit のブロックを返す（投稿に値する中身がなければ []）
  history_entries(digest, ctx)  履歴に残すエントリ（url 必須）
任意:
  HISTORY_DAYS    履歴の保持日数（既定 7）
  MARK_ALL_SEEN   True なら、投稿しなかった候補も「確認済み」として履歴に残す（次回以降の候補から外す）

環境変数:
  CLAUDE_CODE_OAUTH_TOKEN  `claude setup-token` で発行したトークン（Claude のサブスク枠で実行）
  SLACK_WEBHOOK       投稿先の Webhook URL（未設定なら何もせず終了）
  HISTORY_FILE        履歴ファイルのパス
  SLOT                配信枠名（手動実行用。未指定なら現在時刻から判定）
  SCHEDULED           "1" なら定期実行: その枠を今日すでに投稿済みなら何もしない、早く起動したら配信時刻まで待つ
  SCHEDULE_CRON       定期実行を起動した cron 式（github.event.schedule）。遅れて起動しても本来の枠を判定するのに使う
"""

from __future__ import annotations

import calendar
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import ModuleType

import feedparser
import requests

JST = timezone(timedelta(hours=9))
MODEL = "claude-opus-5-5"
USER_AGENT = "Mozilla/5.0 (compatible; news-slack/1.0; +https://github.com/Yuto299/news_slack)"
WEEKDAYS_JA = "月火水木金土日"


@dataclass
class Slot:
    name: str
    time: str  # "HH:MM" (JST)
    label: str
    weekdays: tuple[int, ...] = tuple(range(7))  # 0=月曜


@dataclass
class Item:
    id: int
    source: str
    title: str
    url: str
    summary: str = ""
    published: datetime | None = None
    extra: dict = field(default_factory=dict)

    def to_prompt(self) -> dict:
        d = {"id": self.id, "source": self.source, "title": self.title}
        if self.published:
            d["published"] = self.published.astimezone(JST).strftime("%Y-%m-%d %H:%M")
        if self.summary:
            d["summary"] = self.summary
        d.update(self.extra)
        return d


@dataclass
class Context:
    now: datetime
    slot: Slot
    history: list[dict]
    items: list[Item] = field(default_factory=list)

    @property
    def seen_urls(self) -> set[str]:
        return {h["url"] for h in self.history}

    @property
    def items_by_id(self) -> dict[int, Item]:
        return {i.id: i for i in self.items}

    def recent_titles(self, days: int) -> list[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return [
            h["title"]
            for h in self.history
            if h.get("title") and not h.get("seen_only") and _parse_ts(h["posted_at"]) >= cutoff
        ]

    def header(self) -> dict:
        n = self.now
        text = f"{self.slot.label}　{n:%-m/%-d}({WEEKDAYS_JA[n.weekday()]})"
        return {"type": "header", "text": {"type": "plain_text", "text": text}}


# ---------- 収集 ----------

def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&(nbsp|amp|lt|gt|quot|#\d+);", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def http_get(url: str, **kwargs) -> requests.Response:
    """429 / 5xx のときは 1 回だけ待って再試行する。"""
    for attempt in range(2):
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, **kwargs)
        if resp.status_code in (429, 500, 502, 503) and attempt == 0:
            time.sleep(15)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("unreachable")


def fetch_feeds(
    feeds: dict[str, str],
    lookback_hours: int,
    exclude_urls: set[str],
    summary_chars: int = 300,
    start_id: int = 0,
) -> list[Item]:
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    items: list[Item] = []
    seen = set(exclude_urls)
    for source, feed_url in feeds.items():
        try:
            feed = feedparser.parse(http_get(feed_url).content)
        except Exception as e:  # 1 フィードの失敗で全体を止めない
            print(f"[warn] {source}: {e}", file=sys.stderr)
            continue
        count = 0
        for entry in feed.entries:
            parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            url = entry.get("link", "")
            if not parsed or not url or url in seen:
                continue
            published = datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            if published < since:
                continue
            seen.add(url)
            body = entry.get("summary", "")
            if entry.get("content"):
                body = entry.content[0].get("value", body)
            items.append(
                Item(
                    id=start_id + len(items),
                    source=source,
                    title=entry.get("title", "").strip(),
                    url=url,
                    summary=strip_html(body)[:summary_chars],
                    published=published,
                )
            )
            count += 1
        print(f"[info] {source}: {count} 件", file=sys.stderr)
    return items


# ---------- Claude ----------

def run_claude(system_prompt: str, user_content: str, schema: dict) -> dict:
    """Claude Code CLI（claude -p）経由で呼ぶので、Claude のサブスクリプション枠で動く。"""
    env = os.environ.copy()
    if token := env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        # ターミナルの折り返しごとコピーされた改行・空白を除去
        env["CLAUDE_CODE_OAUTH_TOKEN"] = "".join(token.split())
    proc = subprocess.run(
        [
            "claude", "-p",
            "--model", MODEL,
            "--system-prompt", system_prompt,
            "--json-schema", json.dumps(schema, ensure_ascii=False),
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
    return output["structured_output"]


# ---------- Slack ----------

def esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def link(url: str, text: str) -> str:
    return f"<{url}|{esc(text)}>"


def section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}


def context_block(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3000]}]}


DIVIDER = {"type": "divider"}


def item_text(n: int, url: str, title: str, body: str = "", fields: list[tuple[str, str]] = ()) -> str:
    """1 件分の本文: 太字のリンク付きタイトル、本文、「ラベル：内容」の行。"""
    lines = [f"*{n}. {link(url, title)}*"]
    if body:
        lines.append(esc(body))
    if fields:
        lines.append("")
        lines += [f"{label}：{esc(value)}" for label, value in fields if value]
    return "\n".join(lines)


def post_to_slack(webhook_url: str, fallback_text: str, blocks: list[dict]) -> None:
    resp = requests.post(
        webhook_url,
        json={"text": fallback_text, "blocks": blocks[:50], "unfurl_links": False},
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Slack 投稿失敗: {resp.status_code} {resp.text}")


# ---------- 履歴・配信枠 ----------

def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def load_history(path: str, days: int) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for h in history:
        if _parse_ts(h["posted_at"]) < cutoff:
            continue
        # 旧形式（AI ニュースの初期版）からの移行
        h.setdefault("title", h.get("title_ja", ""))
        h.setdefault("slot", h.get("edition"))
        out.append(h)
    return out


def save_history(path: str, history: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)


def already_posted(history: list[dict], slot: Slot, today: str) -> bool:
    return any(h.get("slot") == slot.name and h.get("date") == today for h in history)


def _slot_dt(slot: Slot, now: datetime) -> datetime:
    hour, minute = map(int, slot.time.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


MAX_DELAY = timedelta(hours=12)  # これ以上遅れて起動した定期実行は、古くなるので投稿しない


def _cron_fire_time(cron: str, now: datetime) -> datetime:
    """cron 式（UTC）の「分 時」から、now 以前で直近の予定起動時刻を返す（JST）。"""
    minute, hour = (int(x) for x in cron.split()[:2])
    utc_now = now.astimezone(timezone.utc)
    fire = utc_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if fire > utc_now + timedelta(minutes=5):
        fire -= timedelta(days=1)
    return fire.astimezone(JST)


def detect_slot(
    slots: list[Slot], now: datetime, scheduled: bool, cron: str = ""
) -> tuple[Slot, datetime] | None:
    """配信枠と、その枠の予定配信日時を返す。

    定期実行: どの cron で起動したか（cron）から枠を決める。GitHub の定期実行は数時間遅れることがあるため、
              実際の起動時刻ではなく予定の起動時刻を基準にする（予定が配信時刻の 30 分前〜90 分後の枠）。
    手動実行: 現在時刻に最も近い枠。
    """
    base = _cron_fire_time(cron, now) if (scheduled and cron) else now
    if scheduled:
        for s in slots:
            for day in (-1, 0, 1):
                slot_dt = _slot_dt(s, base) + timedelta(days=day)
                diff = (base - slot_dt).total_seconds() / 60
                if -30 <= diff <= 90 and slot_dt.weekday() in s.weekdays:
                    return s, slot_dt
        return None

    def distance(s: Slot) -> float:
        d = abs((now - _slot_dt(s, now)).total_seconds())
        return min(d, 86400 - d)

    candidates = [s for s in slots if now.weekday() in s.weekdays] or slots
    slot = min(candidates, key=distance)
    return slot, _slot_dt(slot, now)


def wait_until(slot_dt: datetime) -> None:
    """起動が早すぎた場合、配信時刻 (JST) まで待つ。最大 60 分。"""
    wait = (slot_dt - datetime.now(JST)).total_seconds()
    if 0 < wait <= 3600:
        print(f"[info] {slot_dt:%H:%M} JST まで {int(wait)} 秒待機", file=sys.stderr)
        time.sleep(wait)


# ---------- 実行 ----------

def run(channel: ModuleType, dry_run: bool) -> int:
    now = datetime.now(JST)
    scheduled = os.environ.get("SCHEDULED") == "1"
    webhook_url = os.environ.get("SLACK_WEBHOOK", "")
    if not dry_run and not webhook_url:
        print(f"[warn] {channel.WEBHOOK_ENV} が未設定のため {channel.NAME} はスキップします", file=sys.stderr)
        return 0

    if slot_name := os.environ.get("SLOT"):
        slot = next((s for s in channel.SLOTS if s.name == slot_name), None)
        if slot is None:
            raise ValueError(f"SLOT は {[s.name for s in channel.SLOTS]} のいずれか: {slot_name}")
        slot_dt = _slot_dt(slot, now)
    else:
        detected = detect_slot(channel.SLOTS, now, scheduled, os.environ.get("SCHEDULE_CRON", ""))
        if detected is None:
            print("[info] 配信枠に当たらない起動のためスキップ", file=sys.stderr)
            return 0
        slot, slot_dt = detected
    if scheduled and now - slot_dt > MAX_DELAY:
        print(f"[warn] 予定（{slot_dt:%m/%d %H:%M}）から 12 時間以上遅れて起動したためスキップ", file=sys.stderr)
        return 0
    if scheduled and now - slot_dt > timedelta(minutes=30):
        print(f"[warn] GitHub の定期実行が遅れて起動（予定 {slot_dt:%m/%d %H:%M}）", file=sys.stderr)

    history_file = os.environ.get("HISTORY_FILE", f"history/{channel.NAME}.json")
    history = load_history(history_file, getattr(channel, "HISTORY_DAYS", 7))
    today = slot_dt.strftime("%Y-%m-%d")  # 遅れて日付をまたいでも、本来の配信日の枠として扱う
    if scheduled and already_posted(history, slot, today):
        print(f"[info] {today} の {slot.name} 枠は投稿済みのためスキップ", file=sys.stderr)
        return 0
    print(f"[info] {channel.NAME} / {slot.name} 枠 / 履歴 {len(history)} 件", file=sys.stderr)

    ctx = Context(now=now, slot=slot, history=history)
    ctx.items = channel.collect(ctx)
    print(f"[info] 候補 {len(ctx.items)} 件", file=sys.stderr)

    posted_at = datetime.now(timezone.utc).isoformat()
    marker = {"url": f"marker:{slot.name}:{today}", "posted_at": posted_at, "slot": slot.name,
              "date": today, "seen_only": True}

    if not ctx.items:
        print("[warn] 新しい候補がないため投稿しません", file=sys.stderr)
        if not dry_run:
            save_history(history_file, history + [marker])
        return 0

    digest = run_claude(channel.SYSTEM_PROMPT, channel.build_prompt(ctx.items, ctx), channel.SCHEMA)
    blocks = channel.render(digest, ctx)

    if dry_run:
        print(json.dumps(digest, ensure_ascii=False, indent=2))
        return 0

    entries = [
        {**e, "posted_at": posted_at, "slot": slot.name, "date": today}
        for e in channel.history_entries(digest, ctx)
    ]
    if getattr(channel, "MARK_ALL_SEEN", False):
        posted = {e["url"] for e in entries}
        entries += [
            {"url": i.url, "title": i.title, "posted_at": posted_at, "seen_only": True}
            for i in ctx.items
            if i.url not in posted
        ]

    if not blocks:
        print("[warn] 投稿に値する候補がなかったため投稿しません", file=sys.stderr)
        save_history(history_file, history + entries + [marker])
        return 0

    if scheduled:
        wait_until(slot_dt)
    post_to_slack(webhook_url, f"{slot.label}", blocks)
    save_history(history_file, history + entries + [marker])
    print("[info] Slack に投稿しました", file=sys.stderr)
    return 0
