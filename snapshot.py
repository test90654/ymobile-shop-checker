# -*- coding: utf-8 -*-
"""
県内全店舗の空き状況をまとめて取得して JSON に保存する（GitHub Actions から実行）。
Render の画面はこの JSON を表示する（Render のサーバーからはワイモバイルに直接アクセスできないため）。

  python snapshot.py --pref 埼玉県 --purpose mnp_out --out data
  → data/11_mnp_out.json
"""

import argparse
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ymobile as ym

JST = dt.timezone(dt.timedelta(hours=9))
WORKERS = 3


def snapshot_name(pref, purpose):
    return f"{ym.PREFS.index(pref) + 1:02d}_{purpose}.json"


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


def build(pref, purpose):
    t0 = time.time()
    shops = [s for s in ym.fetch_pref_shops(pref) if s["reservable"]]
    keep = ("code", "name", "city", "address", "tel", "hours", "url", "notice", "lat", "lon")
    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(lambda s: one(s, purpose), shops))
    rows = [dict({k: s.get(k) for k in keep}, **r) for s, r in zip(shops, results)]
    errors = sum(1 for r in rows if "err" in r)
    return {
        "pref": pref,
        "purpose": purpose,
        "purpose_name": ym.PURPOSES[purpose][0],
        "updated_at": dt.datetime.now(JST).isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1),
        "shop_count": len(rows),
        "error_count": errors,
        "shops": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pref", required=True)
    ap.add_argument("--purpose", default=ym.DEFAULT_PURPOSE)
    ap.add_argument("--out", default="data")
    a = ap.parse_args()
    purpose = ym.norm_purpose(a.purpose)
    data = build(a.pref, purpose)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / snapshot_name(a.pref, purpose)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    opened = sum(1 for r in data["shops"] if any(d["starts"] for d in (r.get("days") or [])))
    print(f"::notice title=snapshot::{a.pref} {data['purpose_name']} {data['shop_count']}店 "
          f"空きあり{opened}店 エラー{data['error_count']} {data['seconds']}秒 → {path}")


if __name__ == "__main__":
    main()
