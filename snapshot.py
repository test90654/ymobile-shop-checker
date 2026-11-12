# -*- coding: utf-8 -*-
"""
県内全店舗の空き状況をまとめて取得して JSON に保存する（GitHub Actions から実行）。
Render の画面はこの JSON を表示する（Render のサーバーからはワイモバイルに直接アクセスできないため）。

  python snapshot.py --pref 埼玉県 --purpose mnp_out --out data
  → data/11_mnp_out.json
  python snapshot.py --carrier docomo --pref 埼玉県 --purpose change --out data
  → data/docomo_11_change.json
"""

import argparse
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import docomo
import ymobile as ym

JST = dt.timezone(dt.timedelta(hours=9))
WORKERS = 3


CARRIERS = ("ymobile", "docomo")


def snapshot_name(pref, purpose, carrier="ymobile"):
    base = f"{ym.PREFS.index(pref) + 1:02d}_{purpose}.json"
    return base if carrier == "ymobile" else f"{carrier}_{base}"


def one_docomo(shop, purpose):
    try:
        return {"days": docomo.fetch_days(shop["code"], purpose)}
    except ym.FetchError as e:
        return {"err": str(e)}


def one(shop, purpose):
    try:
        days = ym.fetch_days(shop["code"], purpose)
    except ym.FetchError as e:
        return {"err": str(e)}
    if days is None:
        return {"days": None}
    out = []
    for d in days:
        starts = ym.free_starts(d)
        status = {"0": "holiday", "2": "close"}.get(d.get("openStatus"), "open" if starts else "full")
        out.append({"date": d.get("nowDate"), "status": status, "starts": starts})
    return {"days": out}


def build(pref, purpose, carrier="ymobile"):
    t0 = time.time()
    if carrier == "docomo":
        shops = [s for s in docomo.fetch_pref_shops(pref) if s["reservable"]]
        fn, pname = one_docomo, docomo.PURPOSES[purpose][0]
    else:
        shops = [s for s in ym.fetch_pref_shops(pref) if s["reservable"]]
        fn, pname = one, ym.PURPOSES[purpose][0]
    keep = ("code", "name", "city", "address", "tel", "hours", "url", "notice", "lat", "lon")
    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(lambda s: fn(s, purpose), shops))
    rows = [dict({k: s.get(k) for k in keep}, **r) for s, r in zip(shops, results)]
    errors = sum(1 for r in rows if "err" in r)
    return {
        "carrier": carrier,
        "pref": pref,
        "purpose": purpose,
        "purpose_name": pname,
        "updated_at": dt.datetime.now(JST).isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1),
        "shop_count": len(rows),
        "error_count": errors,
        "shops": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--carrier", default="ymobile", choices=CARRIERS)
    ap.add_argument("--pref", required=True)
    ap.add_argument("--purpose", default="")
    ap.add_argument("--out", default="data")
    a = ap.parse_args()
    purpose = docomo.norm_purpose(a.purpose) if a.carrier == "docomo" else ym.norm_purpose(a.purpose)
    data = build(a.pref, purpose, a.carrier)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / snapshot_name(a.pref, purpose, a.carrier)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    opened = sum(1 for r in data["shops"] if any(d["starts"] for d in (r.get("days") or [])))
    print(f"::notice title=snapshot::{a.carrier} {a.pref} {data['purpose_name']} {data['shop_count']}店 "
          f"空きあり{opened}店 エラー{data['error_count']} {data['seconds']}秒 → {path}")


if __name__ == "__main__":
    main()
