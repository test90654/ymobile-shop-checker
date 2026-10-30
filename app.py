# -*- coding: utf-8 -*-
"""スマホ用の空き状況ビューア（Render で動かす Web アプリ）

Render のサーバーからはワイモバイルのサイトに直接アクセスできない（403）ため、
空き状況の取得は GitHub Actions（snapshot.yml）が行い、結果を data ブランチに保存する。
この画面はその結果を表示し、「最新にする」で GitHub Actions に取得を依頼する。

環境変数
  GITHUB_TOKEN  : このリポジトリの Actions(書き込み) と Contents(読み取り) 権限を持つトークン
  GITHUB_REPO   : 既定 test90654/ymobile-shop-checker
"""

import gzip
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, Response, jsonify, render_template_string, request

import ymobile as ym

app = Flask(__name__)
ROOT = Path(__file__).parent
REPO = os.environ.get("GITHUB_REPO", "test90654/ymobile-shop-checker")
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
SNAP_CACHE_SEC = 20
REFRESH_COOLDOWN_SEC = 120

_lock = threading.Lock()
_snap_cache = {}     # name -> (time, bytes or None)
_last_refresh = {}   # name -> time


def config():
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def snapshot_name(pref, purpose):
    return f"{ym.PREFS.index(pref) + 1:02d}_{purpose}.json"


def gh(method, path, body=None, raw=False):
    headers = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ymobile-shop-checker"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"https://api.github.com{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()


def raw_file(path):
    """raw.githubusercontent.com から読む（回数制限なし・最大5分ほど古い場合あり）"""
    url = f"https://raw.githubusercontent.com/{REPO}/data/{path}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ymobile-shop-checker"}),
                                    timeout=20) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def available():
    """保存済みの [(県, 目的)]"""
    with _lock:
        hit = _snap_cache.get("__list__")
        if hit and time.time() - hit[0] < 60:
            return hit[1]
    names = []
    try:
        _, body = gh("GET", f"/repos/{REPO}/contents/data?ref=data")
        names = [x["name"] for x in json.loads(body)]
    except Exception:  # noqa: BLE001
        cfg = config()  # 取れなければ自動更新の対象から推測
        names = [snapshot_name(p, ym.norm_purpose(u)) for p in cfg.get("snapshot_prefs") or []
                 if p in ym.PREFS for u in cfg.get("snapshot_purposes") or ["mnp_out"]]
    out = []
    for n in names:
        try:
            code, purpose = n[:-5].split("_", 1)
            if purpose in ym.PURPOSES:
                out.append({"pref": ym.PREFS[int(code) - 1], "purpose": purpose,
                            "label": f"{ym.PREFS[int(code) - 1]}・{ym.PURPOSES[purpose][0]}"})
        except (ValueError, IndexError):
            continue
    with _lock:
        _snap_cache["__list__"] = (time.time(), out)
    return out


def load_snapshot(name):
    """data ブランチの JSON（bytes）。なければ None"""
    with _lock:
        hit = _snap_cache.get(name)
        if hit and time.time() - hit[0] < SNAP_CACHE_SEC:
            return hit[1]
    try:
        _, body = gh("GET", f"/repos/{REPO}/contents/data/{name}?ref=data", raw=True)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            body = None
        else:
            # トークンなしだと GitHub API は回数制限（403/429）にかかりやすいので raw で読む
            body = raw_file(f"data/{name}")
    with _lock:
        _snap_cache[name] = (time.time(), body)
    return body


def args():
    pref = request.values.get("pref", "")
    purpose = request.values.get("purpose", ym.DEFAULT_PURPOSE)
    if pref not in ym.PREFS:
        return None, None, (jsonify(error="都道府県名が不正です"), 400)
    if purpose not in ym.PURPOSES:
        return None, None, (jsonify(error="来店目的が不正です"), 400)
    return pref, purpose, None


@app.get("/")
def index():
    cfg = config()
    prefs = cfg.get("snapshot_prefs") or []
    pref = cfg.get("prefecture") or (prefs[0] if prefs else "埼玉県")
    pref = pref if pref in ym.PREFS else "埼玉県"
    return render_template_string(
        PAGE, purposes={k: v[0] for k, v in ym.PURPOSES.items()},
        purpose=ym.norm_purpose(cfg.get("purpose")), prefs=ym.PREFS, pref=pref,
        can_refresh=bool(TOKEN))


@app.get("/api/snapshot")
def api_snapshot():
    pref, purpose, err = args()
    if err:
        return err
    try:
        body = load_snapshot(snapshot_name(pref, purpose))
    except Exception as e:  # noqa: BLE001
        return jsonify(error=f"GitHub から結果を読めませんでした（{e}）"), 502
    if body is None:
        return jsonify(missing=True, available=available())
    if "gzip" in request.headers.get("Accept-Encoding", ""):
        return Response(gzip.compress(body, 6), mimetype="application/json",
                        headers={"Content-Encoding": "gzip", "Cache-Control": "no-store"})
    return Response(body, mimetype="application/json", headers={"Cache-Control": "no-store"})


@app.post("/api/refresh")
def api_refresh():
    pref, purpose, err = args()
    if err:
        return err
    if not TOKEN:
        return jsonify(error="Render に GITHUB_TOKEN が設定されていないため、最新取得を依頼できません"), 503
    name = snapshot_name(pref, purpose)
    with _lock:
        last = _last_refresh.get(name, 0)
        if time.time() - last < REFRESH_COOLDOWN_SEC:
            return jsonify(ok=True, already=True)
        _last_refresh[name] = time.time()
    try:
        gh("POST", f"/repos/{REPO}/actions/workflows/snapshot.yml/dispatches",
           {"ref": "main", "inputs": {"pref": pref, "purpose": purpose}})
    except urllib.error.HTTPError as e:
        with _lock:
            _last_refresh.pop(name, None)
        return jsonify(error=f"GitHub への依頼に失敗しました（HTTP {e.code}）"), 502
    return jsonify(ok=True)


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
  --sat:#3b5bdb; --sun:#c92a2a; --warn:#b26b00;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#0f1114; --card:#1a1d22; --ink:#eef0f3; --sub:#9aa2ad; --line:#2a2e35;
  --accent:#8fb4ff; --on-accent:#0f1114; --ok:#5fd6a0; --okbg:#15322a; --off:#6b727c; --offbg:#22262c;
  --sat:#9db1ff; --sun:#ff8787; --warn:#f0b74f;
}}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;
  padding-bottom:env(safe-area-inset-bottom)}
header{background:var(--accent);color:var(--on-accent);padding:14px 16px}
h1{margin:0 0 4px;font-size:18px}
select,input{display:block;width:100%;font:inherit;font-size:16px;color:var(--ink);background:var(--card);
  border:1px solid var(--line);border-radius:10px;padding:10px 12px}
label{display:block;font-size:12px;opacity:.85;margin:8px 0 4px}
.row2{display:flex;gap:8px}.row2>*{flex:1;min-width:0}
main{padding:10px 16px 40px;max-width:640px;margin:0 auto}
.stat{position:sticky;top:0;z-index:4;background:var(--bg);padding:8px 0 4px}
.line{display:flex;align-items:center;gap:8px}
.stext{flex:1;font-size:13px;color:var(--sub)}
.stext b{color:var(--ink)}
.stext.old b{color:var(--warn)}
.btn{font:inherit;font-size:13px;font-weight:600;border:1px solid var(--line);background:var(--card);color:var(--ink);
  border-radius:9px;padding:6px 10px;cursor:pointer;white-space:nowrap}
.btn:disabled{opacity:.55}
.dates{display:flex;gap:6px;overflow-x:auto;padding:8px 16px 4px;margin:0 -16px;scrollbar-width:none}
.dates::-webkit-scrollbar{display:none}
.dchip{flex:0 0 auto;font:inherit;font-size:13px;border:1px solid var(--line);background:var(--card);color:var(--ink);
  border-radius:20px;padding:5px 11px;cursor:pointer;white-space:nowrap}
.dchip.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.dchip .n{font-size:11px;opacity:.7;margin-left:3px}
.dchip.zero{color:var(--off)}
.opts{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;color:var(--sub);margin:4px 0 10px}
.opts label{display:flex;gap:5px;align-items:center;margin:0;font-size:13px;opacity:1}
.opts input{width:auto;display:inline}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 14px;margin-bottom:10px}
.card h2{font-size:15.5px;margin:0}
.meta{font-size:12.5px;color:var(--sub)}
.notice{font-size:12px;color:var(--warn);margin-top:2px}
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
  <div class="row2">
    <div><label for="pref">都道府県</label>
      <select id="pref">{% for p in prefs %}<option{% if p == pref %} selected{% endif %}>{{ p }}</option>{% endfor %}</select></div>
    <div><label for="city">市区町村・店名で絞る</label>
      <input id="city" placeholder="例: 本庄 / 春日部" autocomplete="off"></div>
  </div>
  <label for="purpose">来店目的</label>
  <select id="purpose">{% for k, v in purposes.items() %}<option value="{{ k }}"{% if k == purpose %} selected{% endif %}>{{ v }}</option>{% endfor %}</select>
</header>
<main>
  <div class="stat">
    <div class="line"><div class="stext" id="stext">読み込み中…</div>
      <button class="btn" id="refresh"{% if not can_refresh %} hidden{% endif %}>↻ 最新にする</button></div>
    <div class="dates" id="dates"></div>
  </div>
  <div class="opts">
    <label><input type="checkbox" id="near"> 現在地から近い順</label>
    <label><input type="checkbox" id="showAll"> 空きのない店舗も表示</label>
  </div>
  <div id="list"></div>
</main>
<script>
const CAN_REFRESH = {{ 'true' if can_refresh else 'false' }};
const WEEK = "日月火水木金土";
const $ = s => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const store = { get(k){ try{ return localStorage.getItem(k); }catch(e){ return null; } }, set(k,v){ try{ localStorage.setItem(k,v); }catch(e){} } };

let snap = null, dateSel = "all", here = null, polling = 0;

for (const id of ["pref","purpose","city"]) { const v = store.get(id); if (v) { const e = $("#"+id); if (e.tagName !== "SELECT" || [...e.options].some(o=>o.value===v)) e.value = v; } }
$("#near").checked = store.get("near") === "1";

function q(){ return `pref=${encodeURIComponent($("#pref").value)}&purpose=${encodeURIComponent($("#purpose").value)}`; }
async function getJSON(url, opt){
  const r = await fetch(url, opt); const j = await r.json().catch(()=>({error:"応答を読めませんでした（サーバー起動中かもしれません。少し待って再読み込みしてください）"}));
  if (!r.ok) throw new Error(j.error || r.status); return j;
}

function label(ymd){ const d = new Date(+ymd.slice(0,4), +ymd.slice(4,6)-1, +ymd.slice(6,8)); return `${d.getMonth()+1}/${d.getDate()}(${WEEK[d.getDay()]})`; }
const hm = t => t.slice(0,2) + ":" + t.slice(2);
const mins = t => +t.slice(0,2)*60 + +t.slice(2);
function group(starts){
  const out = []; let run = [];
  for (const t of starts) { if (run.length && mins(t) - mins(run[run.length-1]) !== 15) { out.push(run); run = []; } run.push(t); }
  if (run.length) out.push(run);
  return out.map(r => r.length === 1 ? hm(r[0]) : `${hm(r[0])}〜${hm(r[r.length-1])}`);
}
function ago(iso){
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "たった今" : m < 60 ? `${m}分前` : `${Math.floor(m/60)}時間${m%60}分前`;
}
function km(s){
  if (!here || s.lat == null) return null;
  const p = Math.PI/180, a = Math.sin((s.lat-here.lat)*p/2)**2 + Math.cos(here.lat*p)*Math.cos(s.lat*p)*Math.sin((s.lon-here.lon)*p/2)**2;
  return 12742 * Math.asin(Math.sqrt(a));
}

async function load(){
  ["pref","purpose","city"].forEach(id => store.set(id, $("#"+id).value));
  const my = ++polling;
  snap = null; dateSel = "all"; render();
  $("#stext").textContent = "読み込み中…（初回はサーバー起動に30秒ほどかかります）";
  try {
    const j = await getJSON(`/api/snapshot?${q()}`);
    if (my !== polling) return;
    if (j.missing) {
      if (CAN_REFRESH) { $("#stext").textContent = "この県・目的のデータはまだないので取得します。"; refresh(true); return; }
      showMissing(j.available || []);
      return;
    }
    snap = j; render();
    if (CAN_REFRESH && (Date.now() - new Date(j.updated_at).getTime()) > 20*60000) refresh(true);
  } catch(e) { if (my === polling) $("#stext").textContent = "⚠ " + e.message; }
}

function showMissing(av){
  $("#stext").textContent = `${$("#pref").value}・${$("#purpose").selectedOptions[0].text} のデータはまだありません。`;
  const list = $("#list"); list.innerHTML = "";
  const box = el("div","card");
  if (av.length) {
    box.append(el("div","meta","保存済みのデータ（タップで表示）"));
    av.forEach(a => {
      const b = el("button","btn", a.label); b.style.margin = "8px 8px 0 0";
      b.onclick = () => { $("#pref").value = a.pref; $("#purpose").value = a.purpose; load(); };
      box.append(b);
    });
  }
  box.append(el("div","meta",
    "ほかの県・目的を見るには、Render に GITHUB_TOKEN を設定して「最新にする」を使うか、" +
    "config.json の snapshot_prefs / snapshot_purposes に追加してください（30分ごとに自動取得）。"));
  box.lastChild.style.marginTop = "10px";
  list.append(box);
}

async function refresh(auto){
  const btn = $("#refresh"); btn.disabled = true;
  const my = ++polling;
  const before = snap ? snap.updated_at : null;
  const base = snap ? statusText() + " ／ " : "";
  try { await getJSON(`/api/refresh?${q()}`, {method: "POST"}); }
  catch(e) { btn.disabled = false; $("#stext").textContent = base + "⚠ " + e.message; return; }
  const t0 = Date.now();
  while (my === polling && Date.now() - t0 < 6*60000) {
    const sec = Math.round((Date.now() - t0) / 1000);
    $("#stext").textContent = base + `GitHubで最新の空きを取得中…（${sec}秒、1〜2分かかります）`;
    await new Promise(r => setTimeout(r, 5000));
    if (my !== polling) return;
    if ((Date.now() - t0) / 1000 < 30) continue;
    try {
      const j = await getJSON(`/api/snapshot?${q()}`);
      if (!j.missing && j.updated_at !== before) { snap = j; btn.disabled = false; render(); return; }
    } catch(e) {}
  }
  if (my === polling) { btn.disabled = false; $("#stext").textContent = base + "取得に時間がかかっています。少ししてから再読み込みしてください"; }
}

function shopsFiltered(){
  const kw = $("#city").value.trim();
  return snap.shops.filter(s => !kw || (s.city + s.name).includes(kw));
}
function startsOf(s){ return (s.days || []).filter(d => dateSel === "all" || d.date === dateSel); }
function firstSlot(s){ for (const d of startsOf(s)) if (d.starts.length) return d.date + d.starts[0]; return null; }
function statusText(){
  const shops = shopsFiltered(), open = shops.filter(s => firstSlot(s) !== null).length;
  return `<b>${ago(snap.updated_at)}</b>の情報・${shops.length}店中 空きあり ${open}店`;
}

function render(){
  const list = $("#list"), box = $("#dates");
  list.innerHTML = ""; box.innerHTML = "";
  if (!snap) return;
  const st = $("#stext"); st.innerHTML = statusText();
  st.classList.toggle("old", Date.now() - new Date(snap.updated_at).getTime() > 30*60000);

  const shops = shopsFiltered();
  const all = new Map();
  shops.forEach(s => (s.days || []).forEach(d => {
    if (!all.has(d.date)) all.set(d.date, 0);
    if (d.starts.length) all.set(d.date, all.get(d.date) + 1);
  }));
  const chip = (key, text, n) => {
    const b = el("button", "dchip" + (dateSel === key ? " on" : "") + (n === 0 ? " zero" : ""), text);
    if (n != null) b.append(el("span","n", n + "店"));
    b.onclick = () => { dateSel = key; render(); };
    box.append(b);
  };
  chip("all", "すべての日", null);
  [...all.keys()].sort().forEach(k => chip(k, label(k), all.get(k)));

  const showAll = $("#showAll").checked, near = $("#near").checked && here;
  const rows = shops.map((s, i) => ({s, i, key: firstSlot(s), km: km(s)}))
    .filter(x => showAll || x.key !== null)
    .sort((a, b) => near ? (a.km ?? 1e9) - (b.km ?? 1e9)
      : (a.key === null) - (b.key === null) || (a.key || "").localeCompare(b.key || "") || a.i - b.i);
  if (!rows.length) { list.append(el("div","empty","条件に合う空きのある店舗はありません")); return; }
  rows.slice(0, 150).forEach(x => list.append(card(x.s, x.km)));
}

function card(s, dist){
  const c = el("div","card");
  c.append(el("h2", null, s.name));
  c.append(el("div","meta", [dist != null ? dist.toFixed(1) + "km" : "", s.city, s.hours].filter(Boolean).join("・")));
  if (s.notice) c.append(el("div","notice","臨時情報：" + s.notice.replace(/\s*<[^>]*>\s*/g, " / ")));
  const box = el("div","days");
  if (s.err) box.append(el("div","err", "取得エラー：" + s.err));
  else if (s.days === null) box.append(el("div","none", "この来店目的はWeb予約を受け付けていません"));
  else {
    const showAll = $("#showAll").checked;
    const ds = startsOf(s).filter(d => d.starts.length || showAll);
    if (!ds.length) box.append(el("div","none", "Webの空きなし（電話で確認）"));
    ds.forEach(d => {
      const row = el("div","d"), lab = label(d.date);
      row.append(el("div", "dl" + (lab.includes("土") ? " sat" : lab.includes("(日)") ? " sun" : ""), lab));
      const ts = el("div","ts");
      if (d.starts.length) group(d.starts).forEach(t => ts.append(el("span","chip", t)));
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

$("#pref").onchange = load;
$("#purpose").onchange = load;
$("#city").oninput = () => { store.set("city", $("#city").value); render(); };
$("#showAll").onchange = render;
$("#refresh").onclick = () => refresh(false);
$("#near").onchange = () => {
  store.set("near", $("#near").checked ? "1" : "0");
  if ($("#near").checked && !here && navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(p => { here = {lat:p.coords.latitude, lon:p.coords.longitude}; render(); },
      () => { $("#near").checked = false; alert("現在地を取得できませんでした"); }, {timeout: 10000});
  } else render();
};
if ($("#near").checked) $("#near").onchange();
setInterval(() => { if (snap && !$("#refresh").disabled) $("#stext").innerHTML = statusText(); }, 30000);
load();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(debug=True, port=5000)
