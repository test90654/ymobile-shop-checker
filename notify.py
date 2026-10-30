# -*- coding: utf-8 -*-
"""
定期チェック（GitHub Actions から実行）
config.json の条件で空き状況を確認し、新しく空いた枠を Discord に通知する。

  python notify.py            # 通常（新しい空きだけ通知）
  python notify.py --test     # 今の空きをそのまま通知（動作確認用）
  python notify.py --diagnose # 接続テスト（通知しない・state を変えない）
"""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

import ymobile as ym

ROOT = Path(__file__).parent
CONFIG = ROOT / "config.json"
STATE = ROOT / "state.json"
JST = dt.timezone(dt.timedelta(hours=9))
FAIL_ALERT_AFTER = 4  # 連続でこの回数取得に失敗したら一度だけ警告を送る


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")


def discord(text):
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("（DISCORD_WEBHOOK_URL が未設定のため送信しません）\n" + text)
        return
    chunks, cur = [], ""
    for line in text.splitlines():  # Discordは1メッセージ2000文字まで
        if len(cur) + len(line) + 1 > 1900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for c in chunks:
        req = urllib.request.Request(
            url, data=json.dumps({"content": c}).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "ymobile-shop-checker"})
        urllib.request.urlopen(req, timeout=15).read()


def select_shops(cfg):
    shops = ym.fetch_shops(cfg.get("lat", 36.2494035), cfg.get("lon", 139.1804534))
    wanted = [str(w).lower() for w in cfg.get("shops") or []]
    if wanted:
        shops = [s for s in shops
                 if any(w == s["code"].lower() or w in s["name"].lower() for w in wanted)]
    return [s for s in shops if s["reservable"]][: int(cfg.get("max_shops", 5))]


def collect(cfg):
    """{ "店コード|日付": ["1000-1045", ...] } と店舗情報を返す"""
    dates = [ym.norm_date(d) for d in cfg.get("dates") or []] or None
    weekdays = set(cfg.get("weekdays") or [])  # 例: ["土", "日"]
    t_from, t_to = ym.norm_time(cfg.get("time_from")), ym.norm_time(cfg.get("time_to"))
    shops = select_shops(cfg)
    found, errors = {}, []
    for s in shops:
        try:
            cal = ym.shop_calendar(s["code"], t_from, t_to, dates)
        except ym.FetchError as e:
            errors.append(f'{s["name"]}: {e}')
            continue
        if cal is None:
            errors.append(f'{s["name"]}: 空き状況データなし')
            continue
        for day in cal:
            if weekdays and day["label"][-2] not in weekdays:
                continue
            if day["slots"]:
                found[f'{s["code"]}|{day["date"]}'] = [f"{a}-{b}" for a, b in day["slots"]]
    return shops, found, errors


def blocks_of(ranges):
    """["1000-1045"] → {"1000","1015","1030"}（15分枠の開始時刻の集合）"""
    out = set()
    for r in ranges:
        a, b = r.split("-")
        m, end = int(a[:2]) * 60 + int(a[2:]), int(b[:2]) * 60 + int(b[2:])
        while m < end:
            out.add(f"{m // 60:02d}{m % 60:02d}")
            m += 15
    return out


def message(title, items, shops):
    names = {s["code"]: s for s in shops}
    lines = [f"📱 **ワイモバイル来店予約：{title}**"]
    for key in sorted(items, key=lambda k: (k.split("|")[1], k)):
        code, date = key.split("|")
        times = [ym.hhmm(t[:4]) + "-" + ym.hhmm(t[5:]) for t in items[key]]
        more = f" ほか{len(times) - 8}枠" if len(times) > 8 else ""
        shop = names.get(code, {"name": code, "url": f"{ym.BASE}/shop/detail/{code}/"})
        lines.append(f"**{ym.fmt_date(date)}** {shop['name']}\n　{' / '.join(times[:8])}{more}\n　<{shop['url']}>")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--diagnose", action="store_true")
    args = ap.parse_args()

    cfg = load(CONFIG, {})
    state = load(STATE, {})
    now = dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")

    if args.diagnose:
        # ::notice:: は Actions の実行結果画面に注釈として表示される
        try:
            shops = ym.fetch_shops(cfg.get("lat", 36.2494035), cfg.get("lon", 139.1804534))
        except ym.FetchError as e:
            print(f"::error title=店舗一覧::取得失敗 {e}")
            sys.exit(1)
        print(f"::notice title=店舗一覧::OK {len(shops)}件")
        for s in shops[:3]:
            try:
                cal = ym.shop_calendar(s["code"])
                res = "データなし" if cal is None else " ".join(d["label"] + d["mark"] for d in cal[:7])
            except ym.FetchError as e:
                res = f"失敗 {e}"
            print(f'::notice title={s["code"]}::{s["name"]} {res}')
        # 1店舗目の各日の空き時間
        if shops:
            cal = ym.shop_calendar(shops[0]["code"]) or []
            for d in cal:
                t = " ".join(f"{ym.hhmm(a)}-{ym.hhmm(b)}" for a, b in d["slots"]) or "なし"
                print(f'::notice title={shops[0]["code"]} {d["label"]}::{t}')
        return

    try:
        shops, found, errors = collect(cfg)
    except ym.FetchError as e:
        shops, found, errors = [], None, [f"店舗一覧: {e}"]

    for e in errors:
        print("⚠", e)

    if found is None or (errors and not found and len(errors) >= len(shops)):
        state["fail_count"] = state.get("fail_count", 0) + 1
        if state["fail_count"] == FAIL_ALERT_AFTER:
            discord("⚠️ ワイモバイルの空き状況を取得できない状態が続いています。\n" + "\n".join(errors[:5]))
        save_state(state)
        print(f"[{now}] 取得失敗（連続{state['fail_count']}回）")
        sys.exit(0)

    prev = state.get("slots", {})
    if args.test:
        discord(message("現在の空き（テスト送信）", found, shops) if found
                else "📱 ワイモバイル来店予約：条件に合う空きは今ありません（テスト送信）")
    else:
        new = {}
        for key, slots in found.items():
            old = blocks_of(prev.get(key, []))
            fresh = [t for t in slots if blocks_of([t]) - old]
            if fresh:
                new[key] = fresh
        if new:
            discord(message("新しく空きが出ました", new, shops))
        print(f"[{now}] 空きあり {len(found)}件 / 新規 {len(new)}件")

    state.update({"slots": found, "fail_count": 0, "last_ok": now})
    save_state(state)


if __name__ == "__main__":
    main()
