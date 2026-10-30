# -*- coding: utf-8 -*-
"""
定期チェック（GitHub Actions から実行）
config.json の条件（来店目的・店舗・日付・時間帯）で予約できる開始時刻を調べ、
新しく空いた時刻を Discord に通知する。

  python notify.py            # 通常（新しく空いた時刻だけ通知）
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


def purpose_of(cfg):
    return ym.norm_purpose(cfg.get("purpose"))


def pname(purpose):
    return ym.PURPOSES[purpose][0]


def base_shops(cfg):
    """prefecture があれば県内全店、なければ lat/lon から近い順"""
    pref = cfg.get("prefecture")
    if pref:
        return ym.fetch_pref_shops(pref)
    return ym.fetch_shops(cfg.get("lat", 36.2494035), cfg.get("lon", 139.1804534))


def select_shops(cfg):
    shops = [s for s in base_shops(cfg) if s["reservable"]]
    cities = [str(c) for c in cfg.get("cities") or []]
    if cities:
        shops = [s for s in shops if any(c in s["city"] for c in cities)]
    wanted = [str(w).lower() for w in cfg.get("shops") or []]
    if wanted:
        shops = [s for s in shops
                 if any(w == s["code"].lower() or w in s["name"].lower() for w in wanted)]
    limit = int(cfg.get("max_shops") or 0)
    return shops[:limit] if limit and not cfg.get("prefecture") else shops


def collect(cfg):
    """{ "店コード|日付": ["HHMM", ...] }（予約できる開始時刻）と店舗一覧・エラー"""
    dates = [ym.norm_date(d) for d in cfg.get("dates") or []] or None
    weekdays = set(cfg.get("weekdays") or [])  # 例: ["土", "日"]
    t_from, t_to = ym.norm_time(cfg.get("time_from")), ym.norm_time(cfg.get("time_to"))
    purpose = purpose_of(cfg)
    shops = select_shops(cfg)
    found, errors = {}, []
    for s in shops:
        try:
            cal = ym.shop_calendar(s["code"], purpose, t_from, t_to, dates)
        except ym.FetchError as e:
            errors.append(f'{s["name"]}: {e}')
            continue
        if cal is None:
            errors.append(f'{s["name"]}: 空き状況データなし')
            continue
        for day in cal:
            if weekdays and day["label"][-2] not in weekdays:
                continue
            if day["starts"]:
                found[f'{s["code"]}|{day["date"]}'] = day["starts"]
    return shops, found, errors


def message(title, items, shops, purpose):
    names = {s["code"]: s for s in shops}
    lines = [f"📱 **ワイモバイル来店予約：{title}**", f"来店目的：{pname(purpose)}"]
    for key in sorted(items, key=lambda k: (k.split("|")[1], k)):
        code, date = key.split("|")
        times = ym.group_starts(items[key])
        shop = names.get(code, {"name": code, "url": f"{ym.BASE}/shop/detail/{code}/"})
        lines.append(f"**{ym.fmt_date(date)}** {shop['name']}\n　{' / '.join(times)}\n　<{shop['url']}>")
    return "\n".join(lines)


def diagnose(cfg):
    # ::notice:: は Actions の実行結果画面に注釈として表示される
    purpose = purpose_of(cfg)
    try:
        shops = select_shops(cfg)
    except ym.FetchError as e:
        print(f"::error title=店舗一覧::取得失敗 {e}")
        sys.exit(1)
    area = cfg.get("prefecture") or "近く"
    print(f"::notice title=店舗一覧::{area} OK {len(shops)}件 / 目的 {pname(purpose)}")
    for s in shops[:3]:
        try:
            cal = ym.shop_calendar(s["code"], purpose) or []
        except ym.FetchError as e:
            print(f'::notice title={s["code"]}::失敗 {e}')
            continue
        for d in cal[:4]:
            print(f'::notice title={s["code"]} {d["label"]}::{" ".join(d["times"]) or "なし"}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--diagnose", action="store_true")
    args = ap.parse_args()

    cfg = load(CONFIG, {})
    if os.environ.get("PREFECTURE_OVERRIDE"):  # Actions の手動実行で県を指定したとき
        cfg["prefecture"] = os.environ["PREFECTURE_OVERRIDE"]
    if args.diagnose:
        diagnose(cfg)
        return

    state = load(STATE, {})
    now = dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    purpose = purpose_of(cfg)

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
        return

    # 来店目的を変えたら前回の状態はリセット（全部が「新しい空き」扱いにならないよう、その回は通知しない）
    prev = state.get("slots", {}) if state.get("purpose") == purpose else None

    if args.test:
        discord(message("現在の空き（テスト送信）", found, shops, purpose) if found
                else f"📱 ワイモバイル来店予約：条件に合う空きは今ありません（{pname(purpose)}・テスト送信）")
    elif prev is not None:
        new = {}
        for key, starts in found.items():
            fresh = [t for t in starts if t not in set(prev.get(key, []))]
            if fresh:
                new[key] = fresh
        if new:
            discord(message("新しく空きが出ました", new, shops, purpose))
        print(f"[{now}] 空きあり {len(found)}件 / 新規 {len(new)}件")
    else:
        print(f"[{now}] 初回（または目的変更）なので状態だけ保存: 空きあり {len(found)}件")

    state.update({"slots": found, "purpose": purpose, "fail_count": 0, "last_ok": now})
    save_state(state)


if __name__ == "__main__":
    main()
