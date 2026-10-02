"""アーカイブ機能を入れる前に投稿した分を、投稿履歴から archive/ に復元する（1 回だけ使う）。

投稿した全文は残っていないので、履歴にあるタイトル・リンク（#ai-news は要約とカテゴリも）だけを書き出す。
使い方: python -m bot.backfill <チャンネル名>=<履歴ファイル> ...
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime

from bot.channels import CHANNELS
from bot.core import JST, WEEKDAYS_JA, append_archive


def _slot_label(channel, entry: dict, posted: datetime) -> str:
    slots = {s.name: s for s in channel.SLOTS}
    if entry.get("slot") in slots:
        return slots[entry["slot"]].label
    if len(channel.SLOTS) == 1:
        return channel.SLOTS[0].label
    # 初期の #ai-news は枠を記録していなかったので、投稿時刻から判定する
    return channel.SLOTS[0].label if posted.hour < 14 else channel.SLOTS[-1].label


def backfill(channel_name: str, history_file: str) -> None:
    channel = CHANNELS[channel_name]
    try:
        with open(history_file, encoding="utf-8") as f:
            history = json.load(f)
    except FileNotFoundError:
        print(f"[warn] {channel_name}: 履歴がありません", file=sys.stderr)
        return

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for h in history:
        if h.get("seen_only"):
            continue  # 紹介せずに確認だけしたもの・配信済みの目印
        posted = datetime.fromisoformat(h["posted_at"]).astimezone(JST)
        date = h.get("date") or posted.strftime("%Y-%m-%d")
        groups[(date, _slot_label(channel, h, posted))].append(h)

    for (date, label), entries in sorted(groups.items()):
        d = datetime.strptime(date, "%Y-%m-%d")
        lines = [
            f"## {label}　{d.month}/{d.day}({WEEKDAYS_JA[d.weekday()]})",
            "> アーカイブ機能を入れる前の投稿を、投稿履歴から復元したものです（タイトル・リンクなど一部のみ）。",
        ]
        for n, h in enumerate(entries, 1):
            title = h.get("title") or h.get("title_ja") or ""
            if h["url"].startswith("http"):
                lines.append(f"**{n}. [{title}]({h['url']})**")
            else:  # 週次まとめなど、リンクのない投稿
                lines.append(f"**{n}. {title}**")
            if h.get("summary"):
                lines.append(h["summary"])
            if h.get("category"):
                lines.append(f"> {h['category']}")
        append_archive(channel_name, date, "\n\n".join(lines) + "\n")
        print(f"[info] {channel_name} {date} {label}: {len(entries)} 件", file=sys.stderr)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        name, path = arg.split("=", 1)
        backfill(name, path)
