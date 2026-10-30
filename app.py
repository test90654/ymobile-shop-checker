# -*- coding: utf-8 -*-
"""スマホ用の空き状況ビューア（Render で動かす Web アプリ）"""

import json
import math
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

import ymobile as ym

app = Flask(__name__)
ROOT = Path(__file__).parent
CACHE_SEC = 120
_cache, _lock = {}, threading.Lock()


def cached(key, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SEC:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def config():
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def dist_km(lat1, lon1, lat2, lon2):
    try:
        lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    except (TypeError, ValueError):
        return None
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


@app.get("/")
def index():
    cfg = config()
    return render_template_string(PAGE, lat=cfg.get("lat", 36.2494035), lon=cfg.get("lon", 139.1804534))


@app.get("/api/shops")
def api_shops():
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    if lat is None or lon is None:
        return jsonify(error="lat/lon が必要です"), 400
    key = f"shops:{lat:.4f},{lon:.4f}"
    try:
        shops = cached(key, lambda: ym.fetch_shops(lat, lon))
    except ym.FetchError as e:
        return jsonify(error=f"店舗一覧を取得できませんでした（{e}）"), 502
    for s in shops:
        d = dist_km(lat, lon, s.get("lat"), s.get("lon"))
        s["km"] = round(d, 1) if d is not None else None
    return jsonify(shops=shops)


@app.get("/api/shop/<code>")
def api_shop(code):
    if not code.isalnum() or len(code) > 12:
        return jsonify(error="店舗コードが不正です"), 400
    try:
        cal = cached(f"cal:{code}", lambda: ym.shop_calendar(code))
    except ym.FetchError as e:
        return jsonify(error=f"取得できませんでした（{e}）"), 502
    if cal is None:
        return jsonify(error="この店舗は空き状況を公開していません"), 404
    for d in cal:
        d["slots"] = [f"{ym.hhmm(a)}-{ym.hhmm(b)}" for a, b in d["slots"]]
    return jsonify(days=cal, checked=time.strftime("%H:%M", time.localtime()))


@app.get("/healthz")
def healthz():
    return "ok"


PAGE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b1f3a">
<title>ワイモバ空き状況</title>
<style>
:root{
  --bg:#f4f5f7; --card:#fff; --ink:#15181d; --sub:#5d6470; --line:#e3e6ea;
  --accent:#0b1f3a; --ok:#0a7d4f; --okbg:#e3f5ec; --few:#9a6200; --fewbg:#fdf1d8;
  --tel:#3b5bdb; --telbg:#e8edff; --off:#9aa1ab; --offbg:#eef0f2;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#0f1114; --card:#1a1d22; --ink:#eef0f3; --sub:#9aa2ad; --line:#2a2e35;
  --accent:#8fb4ff; --ok:#5fd6a0; --okbg:#15322a; --few:#f0b74f; --fewbg:#3a2e14;
  --tel:#9db1ff; --telbg:#1e2742; --off:#6b727c; --offbg:#22262c;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;
  padding:0 0 env(safe-area-inset-bottom)}
header{position:sticky;top:0;z-index:5;background:var(--accent);color:#fff;padding:14px 16px 12px}
@media (prefers-color-scheme: dark){header{color:#0f1114}}
h1{margin:0;font-size:18px;letter-spacing:.02em}
.bar{display:flex;gap:8px;margin-top:10px}
button{font:inherit;border:0;border-radius:10px;padding:9px 12px;cursor:pointer}
.bar button{background:rgba(255,255,255,.16);color:inherit;flex:1}
@media (prefers-color-scheme: dark){.bar button{background:rgba(0,0,0,.14)}}
main{padding:12px 16px 40px;max-width:640px;margin:0 auto}
.legend{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:var(--sub);margin:4px 0 12px}
.legend span b{display:inline-block;width:20px;text-align:center;border-radius:5px;margin-right:3px}
.status{font-size:13px;color:var(--sub);margin:6px 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}
.card h2{font-size:16px;margin:0 0 2px}
.meta{font-size:12.5px;color:var(--sub)}
.notice{font-size:12.5px;color:var(--few);margin-top:4px}
.grid{display:grid;grid-template-columns:repeat(7,1fr);gap:5px;margin-top:12px}
.day{border:0;border-radius:9px;padding:6px 0 5px;text-align:center;font-size:11.5px;line-height:1.25}
.day .m{display:block;font-size:17px;font-weight:700}
.circle{background:var(--okbg);color:var(--ok)} .triangle{background:var(--fewbg);color:var(--few)}
.phone{background:var(--telbg);color:var(--tel)} .holiday,.close{background:var(--offbg);color:var(--off)}
.day.sel{outline:2px solid var(--ink);outline-offset:-2px}
.slots{margin-top:10px;font-size:14px;display:none}
.slots.show{display:block}
.chip{display:inline-block;background:var(--okbg);color:var(--ok);border-radius:8px;padding:3px 8px;margin:3px 4px 0 0;font-variant-numeric:tabular-nums}
.actions{display:flex;gap:8px;margin-top:12px}
.actions a{flex:1;text-align:center;text-decoration:none;border-radius:10px;padding:9px;font-size:14px}
.primary{background:var(--accent);color:#fff}
@media (prefers-color-scheme: dark){.primary{color:#0f1114}}
.ghost{border:1px solid var(--line);color:var(--ink)}
.err{color:#c92a2a;font-size:13px;margin-top:8px}
.skel{height:52px;border-radius:9px;background:var(--offbg);margin-top:12px;animation:p 1.2s infinite}
@keyframes p{50%{opacity:.5}}
</style>
</head>
<body>
<header>
  <h1>ワイモバイル 来店予約の空き</h1>
  <div class="bar">
    <button id="here">📍 現在地の近く</button>
    <button id="home">🏠 いつもの場所</button>
    <button id="reload">↻ 更新</button>
  </div>
</header>
<main>
  <div class="legend">
    <span><b class="circle">◎</b>空きが多い</span><span><b class="triangle">△</b>残りわずか</span>
    <span><b class="phone">☎</b>電話で確認</span><span><b class="holiday">休</b>定休日</span>
  </div>
  <div class="status" id="status">読み込み中…</div>
  <div id="list"></div>
</main>
<script>
const HOME = {lat: {{ lat }}, lon: {{ lon }}};
let where = HOME;
const $ = s => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

function remember(p){ try{ localStorage.setItem("where", JSON.stringify(p)); }catch(e){} }
try{ const s = JSON.parse(localStorage.getItem("where")||"null"); if (s && s.lat) where = s; }catch(e){}

async function getJSON(url){
  const r = await fetch(url); const j = await r.json().catch(()=>({error:"応答を読めませんでした"}));
  if (!r.ok) throw new Error(j.error || r.status); return j;
}

async function load(){
  $("#status").textContent = "店舗を探しています…（初回は起動に30秒ほどかかることがあります）";
  $("#list").innerHTML = "";
  let data;
  try { data = await getJSON(`/api/shops?lat=${where.lat}&lon=${where.lon}`); }
  catch(e){ $("#status").textContent = "⚠ " + e.message; return; }
  const shops = data.shops.filter(s => s.reservable);
  $("#status").textContent = `近い順に ${shops.length} 店舗`;
  // 店ごとに順番に取得（サイトへの負荷を抑える）
  const cards = shops.map(renderShop);
  for (const [i, s] of shops.entries()) await fillShop(s, cards[i]);
}

function renderShop(s){
  const c = el("div","card");
  c.append(el("h2", null, s.name));
  c.append(el("div","meta", [s.km!=null ? `${s.km}km` : "", s.hours, s.tel].filter(Boolean).join("・")));
  if (s.notice) c.append(el("div","notice","臨時情報：" + s.notice));
  const body = el("div"); body.append(el("div","skel")); c.append(body);
  const act = el("div","actions");
  const a1 = el("a","primary","Webで予約"); a1.href = s.url; a1.target = "_blank";
  const a2 = el("a","ghost","電話する"); a2.href = "tel:" + s.tel.replace(/[^0-9]/g,"");
  act.append(a1, a2); c.append(act);
  $("#list").append(c);
  return body;
}

async function fillShop(s, body){
  let data;
  try { data = await getJSON(`/api/shop/${encodeURIComponent(s.code)}`); }
  catch(e){ body.innerHTML = ""; body.append(el("div","err", e.message)); return; }
  body.innerHTML = "";
  const grid = el("div","grid"); const slots = el("div","slots");
  data.days.forEach(d => {
    const b = el("button","day " + d.status);
    b.append(el("span","m", d.mark), document.createTextNode(d.label.replace(/\(.\)/,"")));
    b.onclick = () => {
      grid.querySelectorAll(".sel").forEach(x => x.classList.remove("sel"));
      b.classList.add("sel"); slots.innerHTML = ""; slots.classList.add("show");
      slots.append(el("div","meta", d.label + (d.slots.length ? " の空き" : "")));
      if (d.slots.length) d.slots.forEach(t => slots.append(el("span","chip", t)));
      else slots.append(el("div", null, d.status==="phone" ? "Webの空きはありません（電話で確認）" : "受付していません"));
    };
    grid.append(b);
  });
  body.append(grid, slots);
}

$("#here").onclick = () => {
  if (!navigator.geolocation) return alert("位置情報が使えません");
  $("#status").textContent = "現在地を取得中…";
  navigator.geolocation.getCurrentPosition(
    p => { where = {lat: p.coords.latitude, lon: p.coords.longitude}; remember(where); load(); },
    () => { $("#status").textContent = "⚠ 現在地を取得できませんでした"; },
    {enableHighAccuracy: false, timeout: 10000});
};
$("#home").onclick = () => { where = HOME; remember(where); load(); };
$("#reload").onclick = load;
load();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(debug=True, port=5000)
