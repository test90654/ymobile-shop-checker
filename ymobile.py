# -*- coding: utf-8 -*-
"""ワイモバイル店舗検索・来店予約空き状況の取得ロジック（Web画面と定期チェックで共用）"""

import datetime as dt
import json
import time
import urllib.request

BASE = "https://www.ymobile.jp"

# 業務コード（公式サイトのJSと同じ）。(第1来店目的, 第2来店目的)
WORK_CODES = [("7Y", "2Y"), ("80", "20")]  # ワイモバイルショップ / ソフトバンクショップ

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Content-Type": "application/json; charset=utf-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE,
}

WEEK = "月火水木金土日"
MARK = {"circle": "◎", "triangle": "△", "phone": "☎", "holiday": "休", "close": "×"}

REQUEST_GAP_SEC = 0.8  # サイトに負荷をかけないための待ち時間


class FetchError(Exception):
    pass


def post_json(path, referer):
    req = urllib.request.Request(BASE + path, data=b"", method="POST",
                                 headers=dict(HEADERS, Referer=referer))
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"{type(e).__name__}: {e}") from e
    finally:
        time.sleep(REQUEST_GAP_SEC)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise FetchError(f"JSONではない応答（ブロックされた可能性）: {body[:120]!r}") from e


def fetch_shops(lat, lon):
    path = f"/shop/api_json.php?lon={lon}&lat={lat}"
    data = post_json(path, f"{BASE}/shop/areasearchMap.html?lon={lon}&lat={lat}")
    shops = []
    for s in data.get("shop_data") or []:
        code = s.get("shop_w_code") or (s.get("url") or "").rstrip("/").split("/")[-1]
        if not code:
            continue
        shops.append({
            "code": code,
            "name": s.get("shop_name", code),
            "address": f'{s.get("todofuken_j_name") or ""}{s.get("shi_gun_shima_mei") or ""}{s.get("other_address") or ""}',
            "tel": s.get("telephone") or "",
            "hours": s.get("EIGYO_MIDASHI_1_EX") or "",
            "url": s.get("url") or f"{BASE}/shop/detail/{code}/",
            "reservable": s.get("reservation_flag") == 1,
            "notice": s.get("eigyo_jikan_henko_auto") or "",
            "lat": s.get("lat"),
            "lon": s.get("lon"),
        })
    return shops


def _fetch_days(shop_code, work_cd, referer):
    path = (f"/shop/api/shop_traffic.php?shopId={shop_code}&workCd={work_cd}"
            f"&acceptanceNum=1&inquiryTerm=14")
    data = post_json(path, referer)
    if (data.get("resultInfoDto") or {}).get("resultCd") != "0":
        return None
    blocks = (data.get("responseGetShopRemainBlockDto") or {}).get("shopBlockInfoDtoList") or []
    return (blocks[0].get("dailyBlockInfoDtoList") or []) if blocks else None


def fetch_availability(shop_code, referer=None):
    """2週間分の生データ (days1, days2)。取得できなければ (None, None)。"""
    referer = referer or f"{BASE}/shop/detail/{shop_code}/"
    for cd1, cd2 in WORK_CODES:
        days1 = _fetch_days(shop_code, cd1, referer)
        if days1 is None:
            continue
        return days1, (_fetch_days(shop_code, cd2, referer) or [])
    return None, None


def day_status(day1, day2):
    """公式サイトと同じ判定: circle / triangle / phone / holiday / close"""
    if day1.get("openStatus") == "0":
        return "holiday"
    if day1.get("openStatus") == "2":
        return "close"
    blocks1 = day1.get("blockNumDtoList") or []
    open_flg = any(b.get("acceptanceType") == "02" for b in blocks1)
    not_empty = any(b.get("blockNum") not in (None, 0) for b in blocks1)
    if not (open_flg and not_empty):
        return "phone"
    blocks2 = (day2 or {}).get("blockNumDtoList") or []
    empty = sum(1 for b in blocks2 if b.get("blockNum") == 0)
    filled = sum(1 for b in blocks2 if b.get("blockNum") not in (None, 0))
    return "triangle" if empty >= filled else "circle"


def in_window(st, en, t_from=None, t_to=None):
    return (not t_from or en > t_from) and (not t_to or st < t_to)


def free_slots(day1, t_from=None, t_to=None):
    """空き（blockNum>0）の15分枠を連続ごとにまとめて [(開始, 終了), ...]。時刻は "HHMM"。"""
    out = []
    for b in day1.get("blockNumDtoList") or []:
        if b.get("acceptanceType") != "02" or not b.get("blockNum"):
            continue
        st, en = b["startTime"], b["endTime"]
        if not in_window(st, en, t_from, t_to):
            continue
        if out and out[-1][1] == st:
            out[-1] = (out[-1][0], en)
        else:
            out.append((st, en))
    return out


def shop_calendar(shop_code, t_from=None, t_to=None, dates=None):
    """[{date, label, status, mark, slots:[(st,en)]}] を返す。取得できなければ None。"""
    days1, days2 = fetch_availability(shop_code)
    if days1 is None:
        return None
    by2 = {d.get("nowDate"): d for d in days2}
    out = []
    for d in days1:
        date = d.get("nowDate")
        if dates and date not in dates:
            continue
        st = day_status(d, by2.get(date))
        out.append({
            "date": date,
            "label": fmt_date(date),
            "status": st,
            "mark": MARK[st],
            "slots": free_slots(d, t_from, t_to),
        })
    return out


def hhmm(s):
    return f"{s[:2]}:{s[2:]}"


def fmt_date(yyyymmdd):
    d = dt.datetime.strptime(yyyymmdd, "%Y%m%d").date()
    return f"{d.month}/{d.day}({WEEK[d.weekday()]})"


def norm_date(s, today=None):
    """'11/3' '2026-11-03' '20261103' → '20261103'"""
    today = today or dt.date.today()
    s = str(s).strip().replace("-", "/")
    if len(s) == 8 and s.isdigit():
        return s
    parts = [int(p) for p in s.split("/")]
    if len(parts) == 2:
        m, d = parts
        y = today.year if (m, d) >= (today.month, today.day) else today.year + 1
    else:
        y, m, d = parts
    return f"{y:04d}{m:02d}{d:02d}"


def norm_time(s):
    """'10:00' → '1000'、空なら None"""
    if not s:
        return None
    s = str(s).replace(":", "").strip().zfill(4)
    return s if len(s) == 4 and s.isdigit() else None
