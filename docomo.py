# -*- coding: utf-8 -*-
"""ドコモショップの検索と来店予約（dアカウントなし予約）の空き状況

店舗一覧 : https://shop.smt.docomo.ne.jp/api/shopsearch/shoplist?prefCode=11&page=1 （30件ずつ）
空き状況 : https://reservation.ald.smt.docomo.ne.jp/VIEW_RESERVATION/rif/ns/RI/v1/shops/{店舗コード}
           /visitor-reserve/unserved-list?reservation_reason_code=0002&required_time=60
           &start_date=YYYYMMDD&end_date=YYYYMMDD （1回7日まで）
各時刻の reserved_frame < total_frame なら、その時刻から予約できる（公式の予約画面と同じ判定）。
"""

import datetime as dt
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from ymobile import PREFS, FetchError

SHOP_BASE = "https://shop.smt.docomo.ne.jp"
RSV_API = "https://reservation.ald.smt.docomo.ne.jp/VIEW_RESERVATION/rif/ns/RI/v1/shops"
BOOK_URL = "https://reservation.shop.smt.docomo.ne.jp/visit/login_select.html?shop_code={code}&excd=DS2"

# 来店目的: key -> (表示名, 受付内容コード, 所要時間(分))
# 所要時間は予約画面と同じ値（受付内容マスタと設定XMLの短い方）
PURPOSES = {
    "change":   ("機種変更", "0002", 60),
    "mnp_in":   ("他社からドコモへお乗りかえ", "0065", 30),
    "new":      ("新規申込", "0001", 60),
    "plan":     ("料金プラン変更", "0009", 60),
    "repair":   ("故障", "0004", 60),
    "cancel":   ("解約", "0012", 30),
    "other":    ("その他", "0018", 30),
}
DEFAULT_PURPOSE = "change"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja-JP,ja;q=0.9",
}
REQUEST_GAP_SEC = 0.6
DAYS = 14
JST = dt.timezone(dt.timedelta(hours=9))
SAME_DAY_MARGIN_MIN = 15  # 当日は開始15分前まで（公式の deadline_for_same_day_reservation=-15）


# ドコモのサーバーは古い TLS 再ネゴシエーションを使うため、OpenSSL 3 の既定では接続できない。
# 証明書の検証はそのままで、レガシー再ネゴシエーションだけ許可する。
_SSL = ssl.create_default_context()
_SSL.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)


def norm_purpose(p):
    return p if p in PURPOSES else DEFAULT_PURPOSE


def get_json(url, referer):
    req = urllib.request.Request(url, headers=dict(HEADERS, Referer=referer))
    try:
        with urllib.request.urlopen(req, timeout=20, context=_SSL) as r:
            body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"{type(e).__name__}: {e}") from e
    finally:
        time.sleep(REQUEST_GAP_SEC)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise FetchError(f"JSONではない応答（ブロックされた可能性）: {body[:120]!r}") from e


def _hours(s):
    a, b = s.get("shophour_start1") or "", s.get("shophour_end1") or ""
    return f"{a[:2]}:{a[2:]}～{b[:2]}:{b[2:]}" if len(a) == 4 and len(b) == 4 else ""


def _latlon(s):
    try:
        lat = s["north_latitude"] + s["north_latitude_min"] / 60 + s["north_latitude_sec"] / 3600 / 1000
        lon = s["east_longitude"] + s["east_longitude_min"] / 60 + s["east_longitude_sec"] / 3600 / 1000
        return round(lat, 6), round(lon, 6)
    except (KeyError, TypeError):
        return None, None


def _notice(s):
    parts = []
    for i in range(1, 6):
        d = s.get(f"temp_holiday{i}")
        if d:
            parts.append(f"{int(d[4:6])}/{int(d[6:8])}休業" if len(d) == 8 and d.isdigit() else f"{d}休業")
    return " / ".join(parts)


def _shop(s):
    code = f'{s.get("shop_code", "")}{s.get("add_shop_code", "")}'
    if len(code) != 13:
        return None
    lat, lon = _latlon(s)
    tel = s.get("customer_tel") or s.get("free_tel") or ""
    return {
        "code": code,
        "name": s.get("shop_name", code),
        "city": s.get("municipality") or "",
        "address": "".join(x or "" for x in (s.get("prefecture"), s.get("municipality"), s.get("address"),
                                              s.get("address_building"))),
        "tel": tel,
        "hours": _hours(s),
        "url": BOOK_URL.format(code=code),
        "reservable": s.get("shop_appointment") == 1,
        "notice": _notice(s),
        "lat": lat,
        "lon": lon,
    }


def fetch_pref_shops(pref):
    if pref not in PREFS:
        raise FetchError(f"都道府県名が不正です: {pref}")
    code = PREFS.index(pref) + 1  # JIS 都道府県コード
    shops, page = [], 1
    while True:
        data = get_json(f"{SHOP_BASE}/api/shopsearch/shoplist?prefCode={code}&page={page}",
                        f"{SHOP_BASE}/result.html?prefCode={code}")
        lst = data.get("list") or []
        shops += [x for x in (_shop(s) for s in lst) if x]
        if not lst or len(shops) >= int(data.get("total") or 0) or page >= 20:
            break
        page += 1
    return shops


def fetch_days(shop_code, purpose=DEFAULT_PURPOSE, today=None):
    """[{date, status, starts:["HHMM", ...]}]（今日から14日分）。予約を受け付けていなければ None"""
    _, reason, minutes = PURPOSES[norm_purpose(purpose)]
    now = dt.datetime.now(JST)
    today = today or now.date()
    referer = "https://reservation.shop.smt.docomo.ne.jp/"
    out = []
    for w in range(0, DAYS, 7):
        s = today + dt.timedelta(days=w)
        e = s + dt.timedelta(days=6)
        q = urllib.parse.urlencode({"reservation_reason_code": reason, "required_time": minutes,
                                    "start_date": s.strftime("%Y%m%d"), "end_date": e.strftime("%Y%m%d")})
        try:
            data = get_json(f"{RSV_API}/{shop_code}/visitor-reserve/unserved-list?{q}", referer)
        except FetchError as err:
            if "HTTP 4" in str(err):
                return None if not out else out  # この目的を受け付けていない店舗など
            raise
        for d in data.get("date_list") or []:
            date = d.get("date")
            if d.get("is_holiday") or d.get("is_excluded_day"):
                out.append({"date": date, "status": "holiday", "starts": []})
                continue
            starts = []
            for t in d.get("time_list") or []:
                total, reserved = int(t.get("total_frame") or 0), int(t.get("reserved_frame") or 0)
                if total <= 0 or reserved >= total:
                    continue
                hhmm = (t.get("time") or "").replace(":", "")
                if date == now.strftime("%Y%m%d"):
                    slot = dt.datetime.strptime(date + hhmm, "%Y%m%d%H%M").replace(tzinfo=JST)
                    if slot - dt.timedelta(minutes=SAME_DAY_MARGIN_MIN) < now:
                        continue
                starts.append(hhmm)
            out.append({"date": date, "status": "open" if starts else "full", "starts": starts})
    return out
