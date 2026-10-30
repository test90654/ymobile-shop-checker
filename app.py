# -*- coding: utf-8 -*-
"""スマホ用の空き状況ビューア（Render で動かす Web アプリ）

都道府県を選ぶと県内の全店舗の空きをまとめて調べ、空きのある店舗だけを早い順に表示する。
"""

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
        if len(_cache) > 2000:  # 古いものを掃除
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:500]:
                _cache.pop(k, None)
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
    pref = cfg.get("prefecture") if cfg.get("prefecture") in ym.PREFS else "埼玉県"
    return render_template_string(
        PAGE, lat=cfg.get("lat", 36.2494035), lon=cfg.get("lon", 139.1804534),
        purposes={k: v[0] for k, v in ym.PURPOSES.items()},
        purpose=ym.norm_purpose(cfg.get("purpose")), prefs=ym.PREFS, pref=pref)


@app.get("/api/shops")
def api_shops():
    pref = request.args.get("pref")
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    try:
        if pref:
            if pref not in ym.PREFS:
                return jsonify(error="都道府県名が不正です"), 400
            shops = cached(f"pref:{pref}", lambda: ym.fetch_pref_shops(pref))
        elif lat is not None and lon is not None:
            shops = cached(f"near:{lat:.4f},{lon:.4f}", lambda: ym.fetch_shops(lat, lon))
        else:
            return jsonify(error="pref か lat/lon が必要です"), 400
    except ym.FetchError as e:
        return jsonify(error=f"店舗一覧を取得できませんでした（{e}）"), 502
    out = []
    for s in shops:
        s = dict(s)
        if lat is not None and lon is not None:
            d = dist_km(lat, lon, s.get("lat"), s.get("lon"))
            s["km"] = round(d, 1) if d is not None else None
        out.append(s)
    return jsonify(shops=out)


@app.get("/api/shop/<code>")
def api_shop(code):
    if not code.isalnum() or len(code) > 12:
        return jsonify(error="店舗コードが不正です"), 400
    purpose = request.args.get("purpose", ym.DEFAULT_PURPOSE)
    if purpose not in ym.PURPOSES:
        return jsonify(error="来店目的が不正です"), 400
    try:
        cal = cached(f"cal:{code}:{purpose}", lambda: ym.shop_calendar(code, purpose))
    except ym.FetchError as e:
        return jsonify(error=f"取得できませんでした（{e}）"), 502
    if cal is None:
        return jsonify(days=None)  # この目的は受け付けていない店舗
    return jsonify(days=cal)


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
  --accent:#0b1f3a; --on-accent:#fff; --ok:#0a7d4f; --okbg:#e3f5ec; --off:#9aa1ab; --offbg:#eef0f2;
  --sat:#3b5bdb; --sun:#c92a2a; --bar:#0a7d4f;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#0f1114; --card:#1a1d22; --ink:#eef0f3; --sub:#9aa2ad; --line:#2a2e35;
  --accent:#8fb4ff; --on-accent:#0f1114; --ok:#5fd6a0; --okbg:#15322a; --off:#6b727c; --offbg:#22262c;
  --sat:#9db1ff; --sun:#ff8787; --bar:#5fd6a0;
}}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;
  padding-bottom:env(safe-area-inset-bottom)}
header{background:var(--accent);color:var(--on-accent);padding:14px 16px 14px}
h1{margin:0 0 10px;font-size:18px}
.seg{display:flex;background:rgba(255,255,255,.14);border-radius:10px;padding:3px;margin-bottom:10px}
@media (prefers-color-scheme: dark){.seg{background:rgba(0,0,0,.14)}}
.seg button{flex:1;font:inherit;font-size:14px;border:0;border-radius:8px;padding:7px;background:none;color:inherit;cursor:pointer}
.seg button.on{background:var(--card);color:var(--ink);font-weight:600}
select,input{display:block;width:100%;font:inherit;font-size:16px;color:var(--ink);background:var(--card);
  border:1px solid var(--line);border-radius:10px;padding:10px 12px}
label{display:block;font-size:12px;opacity:.85;margin:8px 0 4px}
.row2{display:flex;gap:8px}.row2>*{flex:1;min-width:0}
.go{display:block;width:100%;margin-top:12px;font:inherit;font-weight:700;border:0;border-radius:10px;padding:11px;
  background:var(--card);color:var(--ink);cursor:pointer}
main{padding:12px 16px 40px;max-width:640px;margin:0 auto}
.prog{position:sticky;top:0;z-index:4;background:var(--bg);padding:8px 0 8px}
.ptext{font-size:13px;color:var(--sub)}
.pbar{height:4px;background:var(--offbg);border-radius:4px;overflow:hidden;margin-top:6px}
.pbar i{display:block;height:100%;width:0;background:var(--bar);transition:width .2s}
.dates{display:flex;gap:6px;overflow-x:auto;padding:8px 0 4px;margin:0 -16px;padding-left:16px;padding-right:16px;scrollbar-width:none}
.dates::-webkit-scrollbar{display:none}
.dchip{flex:0 0 auto;font:inherit;font-size:13px;border:1px solid var(--line);background:var(--card);color:var(--ink);
  border-radius:20px;padding:5px 11px;cursor:pointer;white-space:nowrap}
.dchip.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.dchip .n{font-size:11px;opacity:.7;margin-left:3px}
.dchip.zero{color:var(--off)}
.opts{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--sub);margin:6px 0 10px}
.opts input{width:auto;display:inline}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 14px;margin-bottom:10px}
.card h2{font-size:15.5px;margin:0}
.meta{font-size:12.5px;color:var(--sub)}
.notice{font-size:12px;color:#b26b00;margin-top:2px}
.days{margin-top:8px}
.d{display:flex;gap:10px;align-items:flex-start;padding:6px 0;border-top:1px solid var(--line)}
.dl{flex:0 0 66px;font-weight:600;font-size:14px;padding-top:2px;font-variant-numeric:tabular-nums}
.dl.sat{color:var(--sat)} .dl.sun{color:var(--sun)}
.ts{flex:1;display:flex;flex-wrap:wrap;gap:5px}
.chip{background:var(--okbg);color:var(--ok);border-radius:8px;padding:2px 8px;font-size:14px;font-weight:600;font-variant-numeric:tabular-nums}
.none{color:var(--off);font-size:13px;padding-top:2px}
.acts{display:flex;gap:8px;margin-top:10px}
.acts a{flex:1;text-align:center;text-decoration:none;border-radius:10px;padding:8px;font-size:14px}
.primary{background:var(--accent);color:var(--on-accent)}
.ghost{border:1px solid var(--line);color:var(--ink)}
.empty{text-align:center;color:var(--sub);padding:28px 0;font-size:14px}
.err{color:var(--sun);font-size:13px}
</style>
</head>
<body>
<header>
  <h1>ワイモバイル 来店予約の空き</h1>
  <div class="seg" id="mode">
    <button data-m="pref">都道府県でまとめて</button>
    <button data-m="near">現在地の近く</button>
  </div>
  <div id="prefBox" class="row2">
    <div><label for="pref">都道府県</label>
      <select id="pref">{% for p in prefs %}<option{% if p == pref %} selected{% endif %}>{{ p }}</option>{% endfor %}</select></div>
    <div><label for="city">市区町村で絞る（任意）</label>
      <input id="city" placeholder="例: 本庄 / さいたま" autocomplete="off"></div>
  </div>
  <label for="purpose">来店目的</label>
  <select id="purpose">{% for k, v in purposes.items() %}<option value="{{ k }}"{% if k == purpose %} selected{% endif %}>{{ v }}</option>{% endfor %}</select>
  <button class="go" id="go">この条件で探す</button>
</header>
<main>
  <div class="prog" id="prog" hidden>
    <div class="ptext" id="ptext"></div>
    <div class="pbar"><i id="pbar"></i></div>
    <div class="dates" id="dates"></div>
  </div>
  <div class="opts" id="opts" hidden><label style="margin:0;opacity:1;display:flex;gap:6px;align-items:center">
    <input type="checkbox" id="showAll"> 空きのない店舗も表示</label></div>
  <div id="list"><div class="empty">条件を選んで「この条件で探す」を押してください</div></div>
</main>
<script>
const HOME = {lat: {{ lat }}, lon: {{ lon }}};
const PARALLEL = 3;
const $ = s => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const store = { get(k){ try{ return localStorage.getItem(k); }catch(e){ return null; } }, set(k,v){ try{ localStorage.setItem(k,v); }catch(e){} } };

let mode = store.get("mode") || "pref";
let run = 0;            // 条件を変えたら古い検索結果を捨てるための番号
let results = [];       // [{shop, days, err}]
let dateSel = "all";

for (const id of ["pref","purpose","city"]) { const v = store.get(id); if (v != null && v !== "") { const e = $("#"+id); if (e.tagName !== "SELECT" || [...e.options].some(o=>o.value===v)) e.value = v; } }
function setMode(m){ mode = m; store.set("mode", m);
  document.querySelectorAll("#mode button").forEach(b => b.classList.toggle("on", b.dataset.m === m));
  $("#prefBox").style.display = m === "pref" ? "" : "none"; }
document.querySelectorAll("#mode button").forEach(b => b.onclick = () => setMode(b.dataset.m));
setMode(mode);

async function getJSON(url){
  const r = await fetch(url); const j = await r.json().catch(()=>({error:"応答を読めませんでした（サーバー起動中かもしれません）"}));
  if (!r.ok) throw new Error(j.error || r.status); return j;
}
function position(){
  return new Promise((ok, ng) => {
    if (!navigator.geolocation) return ng(new Error("位置情報が使えません"));
    navigator.geolocation.getCurrentPosition(p => ok({lat:p.coords.latitude, lon:p.coords.longitude}),
      () => ng(new Error("現在地を取得できませんでした")), {timeout: 10000});
  });
}

$("#go").onclick = search;
$("#showAll").onchange = render;

async function search(){
  const my = ++run;
  ["pref","purpose","city"].forEach(id => store.set(id, $("#"+id).value));
  results = []; dateSel = "all";
  $("#list").innerHTML = ""; $("#prog").hidden = false; $("#opts").hidden = true; $("#dates").innerHTML = "";
  setProg("店舗一覧を取得しています…（初回はサーバー起動に30秒ほどかかります）", 0);
  let shops;
  try {
    if (mode === "pref") {
      shops = (await getJSON(`/api/shops?pref=${encodeURIComponent($("#pref").value)}`)).shops;
    } else {
      setProg("現在地を取得しています…", 0);
      let pos; try { pos = await position(); } catch(e) { pos = HOME; }
      shops = (await getJSON(`/api/shops?lat=${pos.lat}&lon=${pos.lon}`)).shops;
    }
  } catch(e) { if (my === run) setProg("⚠ " + e.message, 0); return; }
  if (my !== run) return;
  const city = $("#city").value.trim();
  shops = shops.filter(s => s.reservable && (mode !== "pref" || !city || (s.city + s.name).includes(city)));
  if (!shops.length) { setProg("条件に合う店舗がありません", 1); return; }

  const purpose = $("#purpose").value;
  let done = 0, i = 0;
  const worker = async () => {
    while (i < shops.length && my === run) {
      const s = shops[i++];
      let days = null, err = null;
      try { days = (await getJSON(`/api/shop/${encodeURIComponent(s.code)}?purpose=${purpose}`)).days; }
      catch(e) { err = e.message; }
      if (my !== run) return;
      results.push({shop: s, days, err, order: shops.indexOf(s)});
      done++;
      setProg(progText(done, shops.length), done / shops.length);
      render();
    }
  };
  await Promise.all(Array.from({length: PARALLEL}, worker));
  if (my === run) { setProg(progText(done, shops.length, true), 1); render(); }
}

function openCount(){ return results.filter(r => firstSlot(r) !== null).length; }
function progText(done, total, fin){
  const n = openCount();
  return fin ? `${total}店舗を確認しました。空きがあるのは ${n}店舗`
             : `${done} / ${total} 店舗を確認中… 空きあり ${n}店舗`;
}
function setProg(t, ratio){ $("#ptext").textContent = t; $("#pbar").style.width = Math.round(ratio*100) + "%"; }

function daysOf(r){ return (r.days || []).filter(d => dateSel === "all" || d.date === dateSel); }
function firstSlot(r){
  for (const d of daysOf(r)) if (d.starts.length) return d.date + d.starts[0];
  return null;
}

function renderDates(){
  const box = $("#dates"); const all = new Map();
  results.forEach(r => (r.days || []).forEach(d => {
    if (!all.has(d.date)) all.set(d.date, {label: d.label, n: 0});
    if (d.starts.length) all.get(d.date).n++;
  }));
  const keys = [...all.keys()].sort();
  box.innerHTML = "";
  const mk = (key, text, n) => {
    const b = el("button", "dchip" + (dateSel === key ? " on" : "") + (n === 0 ? " zero" : ""), text);
    if (n != null) b.append(el("span","n", n + "店"));
    b.onclick = () => { dateSel = key; render(); };
    box.append(b);
  };
  mk("all", "すべての日", null);
  keys.forEach(k => mk(k, all.get(k).label, all.get(k).n));
}

function render(){
  renderDates();
  $("#opts").hidden = results.length === 0;
  const showAll = $("#showAll").checked;
  const list = $("#list"); list.innerHTML = "";
  const rows = results
    .map(r => ({r, key: firstSlot(r)}))
    .filter(x => showAll || x.key !== null)
    .sort((a, b) => (a.key === null) - (b.key === null) || (a.key || "").localeCompare(b.key || "") || a.r.order - b.r.order);
  if (!rows.length) { list.append(el("div","empty", results.length ? "今のところ空きのある店舗はありません" : "")); return; }
  rows.forEach(({r}) => list.append(card(r)));
}

function card(r){
  const s = r.shop, c = el("div","card");
  c.append(el("h2", null, s.name));
  c.append(el("div","meta", [s.km != null ? s.km + "km" : "", s.city, s.hours].filter(Boolean).join("・")));
  if (s.notice) c.append(el("div","notice","臨時情報：" + s.notice));
  const box = el("div","days");
  if (r.err) box.append(el("div","err", r.err));
  else if (r.days === null) box.append(el("div","none", "この来店目的はWeb予約を受け付けていません"));
  else {
    const ds = daysOf(r).filter(d => d.starts.length || $("#showAll").checked);
    if (!ds.length) box.append(el("div","none", "Webの空きなし（電話で確認）"));
    ds.forEach(d => {
      const row = el("div","d");
      row.append(el("div", "dl" + (/土/.test(d.label) ? " sat" : /日\)/.test(d.label) ? " sun" : ""), d.label));
      const ts = el("div","ts");
      if (d.times.length) d.times.forEach(t => ts.append(el("span","chip", t)));
      else ts.append(el("span","none", d.status === "holiday" ? "定休日" : "空きなし"));
      row.append(ts); box.append(row);
    });
  }
  c.append(box);
  const a = el("div","acts");
  const a1 = el("a","primary","Webで予約"); a1.href = s.url; a1.target = "_blank"; a1.rel = "noopener";
  const a2 = el("a","ghost","電話する"); a2.href = "tel:" + (s.tel || "").replace(/[^0-9]/g,"");
  a.append(a1, a2); c.append(a);
  return c;
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(debug=True, port=5000)
