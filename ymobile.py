# -*- coding: utf-8 -*-
"""ワイモバイル店舗検索・来店予約空き状況の取得ロジック（Web画面と定期チェックで共用）"""

import datetime as dt
import json
import time
import urllib.request

BASE = "https://www.ymobile.jp"

# 来店目的（業務コード）。目的ごとに所要時間と空き枠が違う。
# かんたん来店予約（visit-reservation.mb.softbank.jp）の getWorkInfo で確認したもの
PURPOSES = {
    "I0": "MNP予約番号発行（他社へのお乗り換え）",
    "20": "機種変更",
    "K0": "他社⇒ワイモバイル（MNP）",
    "J0": "ソフトバンク⇒ワイモバイル",
    "E0": "ワイモバイル⇒ソフトバンク",
    "30": "他社⇒ソフトバンク（MNP）",
    "10": "新規契約",
    "B0": "契約内容確認/変更",
    "H0": "端末返却・下取り",
    "60": "故障修理",
    "80": "スマホ・ケータイの解約",
    "N0": "店頭スマホサポート（データ移行）",
    "L0": "店頭スマホサポート（データ移行以外）",
}
DEFAULT_PURPOSE = "I0"
FALLBACK_CODES = ["7Y"]  # ワイモバイル専売店で上の業務コードが使えない場合

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


def fetch_days(shop_code, purpose=DEFAULT_PURPOSE):
    """指定した来店目的の2週間分の生データ。取得できなければ None。"""
    referer = f"{BASE}/shop/detail/{shop_code}/"
    for cd in [purpose] + FALLBACK_CODES:
        days = _fetch_days(shop_code, cd, referer)
        if days is not None:
            return days
    return None


def free_starts(day, t_from=None, t_to=None):
    """予約できる開始時刻（"HHMM"）の一覧"""
    out = []
    for b in day.get("blockNumDtoList") or []:
        if b.get("acceptanceType") != "02" or not b.get("blockNum"):
            continue
        st = b["startTime"]
        if (t_from and st < t_from) or (t_to and st > t_to):
            continue
        out.append(st)
    return out


def _min(t):
    return int(t[:2]) * 60 + int(t[2:])


def group_starts(starts):
    """["1400","1415","1430","1515"] → ["14:00〜14:30", "15:15"]（15分おきに続く時刻をまとめる）"""
    out, run = [], []
    for t in starts:
        if run and _min(t) - _min(run[-1]) != 15:
            out.append(run)
            run = []
        run.append(t)
    if run:
        out.append(run)
    return [hhmm(r[0]) if len(r) == 1 else f"{hhmm(r[0])}〜{hhmm(r[-1])}" for r in out]


def shop_calendar(shop_code, purpose=DEFAULT_PURPOSE, t_from=None, t_to=None, dates=None):
    """[{date, label, status, starts:["HHMM",...], times:["14:00〜18:45",...]}]。取得できなければ None"""
    days = fetch_days(shop_code, purpose)
    if days is None:
        return None
    out = []
    for d in days:
        date = d.get("nowDate")
        if dates and date not in dates:
            continue
        starts = free_starts(d, t_from, t_to)
        status = {"0": "holiday", "2": "close"}.get(d.get("openStatus"), "open" if starts else "full")
        out.append({"date": date, "label": fmt_date(date), "status": status,
                    "starts": starts, "times": group_starts(starts)})
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
