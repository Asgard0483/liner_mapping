"""HTML-Vergleichsbericht (eigenständige Datei, ohne bpy und ohne Internet).

Die Zahlen werden in Python berechnet und als JSON eingebettet; das Zeichnen
der Diagramme (SVG) und die Tooltips übernimmt ein kleines Skript im Bericht.
"""

import datetime
import json
import math

from . import colors
from .compare import ANGLE_NAMES, GRID_THETAS, grid_heights, grid_thetas, overlap
from .database import META_FIELDS
from .geometry import SIDE_NAMES

PROFILE_DZ_MM = 5.0
SECTION_ANGLES = 72
DIFF_DZ_MM = 10.0


def _counts_text(counts):
    lo, hi = min(counts), max(counts)
    return str(lo) if lo == hi else "%d–%d" % (lo, hi)


def _r(v, nd=2):
    return None if v is None else round(v, nd)


def report_data(comp, title="Liner-Vergleich"):
    records, models, labels = comp["records"], comp["models"], comp["labels"]

    liners = []
    for i, (rec, m, label) in enumerate(zip(records, models, labels)):
        zs = grid_heights(m.z_min, m.z_max, PROFILE_DZ_MM)
        prof = [m.side_values(z) for z in zs]
        liners.append({
            "label": label,
            "slot": i,
            "meta": {k: rec.get(k, "") for k in META_FIELDS},
            "nenn": rec.get("nenn_wandstaerke_mm") or 0.0,
            "seite": "rechts" if rec.get("seite") == "RIGHT" else "links",
            "bezug": "außen" if rec.get("umfang_bezug") == "OUTER" else "innen",
            "distal": m.distal_mm,
            "laenge": rec.get("laenge_cm") or 0.0,
            "punkte_je_hoehe": _counts_text(m.counts),
            # gemessene Werte genau in den Richtungen A, M, P, L (für die Verlaufsdiagramme)
            "mess": [[{"h": _r(h), "v": _r(t[k * len(t) // 4])} for h, t, _ in m.rows
                      if (k * len(t)) % 4 == 0] for k in range(4)],
            "profil": {"h": [_r(z / 10.0) for z in zs],
                       "t": [[_r(p[k]) for p in prof] for k in range(4)]},
        })

    # Querschnitte an allen gemessenen Höhen (Vereinigung)
    sec_heights = sorted({h for m in models for h in m.hs})
    angles = [2.0 * math.pi * j / SECTION_ANGLES for j in range(SECTION_ANGLES)]
    sections = []
    for z in sec_heights:
        per = []
        for m in models:
            if m.z_min - 1e-6 <= z <= m.z_max + 1e-6:
                ri = m.radius_in(z)
                vals = m.side_values(z)
                per.append({
                    "ri": _r(ri),
                    "t": [_r(m.thickness(z, a)) for a in angles],
                    "seiten": [_r(v) for v in vals],
                    "umfang": _r(m.circumference_in_cm(z)),
                })
            else:
                per.append(None)
        sections.append({"h": _r(z / 10.0), "liner": per})

    # Differenzkarten gegenüber der Referenz
    ref = models[0]
    diffs, lim = [], 0.0
    ths = grid_thetas()
    for m in models[1:]:
        ov = overlap(ref, m)
        if ov is None:
            diffs.append(None)
            continue
        zs = grid_heights(ov[0], ov[1], DIFF_DZ_MM)
        grid = []
        for z in zs:
            row = []
            for th in ths:
                a, b = ref.thickness(z, th), m.thickness(z, th)
                row.append([_r(b - a), _r(a), _r(b)])
                lim = max(lim, abs(b - a))
            grid.append(row)
        diffs.append({"h": [_r(z / 10.0) for z in zs], "werte": grid})

    points = [{"h": h, "w": label, "v": [_r(v) for v in vals], "gem": meas}
              for h, _deg, label, vals, meas in comp["points"]]

    def clean(d):
        return None if d is None else {k: (_r(v) if isinstance(v, float) else v)
                                       for k, v in d.items()}

    return {
        "titel": title,
        "erstellt": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
        "gewicht_wand": comp["wall_weight"],
        "liner": liners,
        "paare": [clean(p) for p in comp["pairs"]],
        "matrix": [[_r(v, 1) for v in row] for row in comp["matrix"]],
        "querschnitte": sections,
        "differenzen": diffs,
        "diff_limit": _r(max(lim, 0.5), 1),
        "punkte": points,
        "seiten": list(SIDE_NAMES),
        "winkel": list(ANGLE_NAMES),
        "n_winkel": GRID_THETAS,
        "farben_hell": list(colors.CATEGORICAL_LIGHT),
        "farben_dunkel": list(colors.CATEGORICAL_DARK),
        "sequenziell": list(colors.SEQUENTIAL),
    }


def render_html(comp, title="Liner-Vergleich"):
    data = report_data(comp, title)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (_TEMPLATE.replace("__TITLE__", _esc(title))
            .replace("__DATA__", payload))


def write_report(comp, path, title="Liner-Vergleich"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(comp, title))
    return path


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100;
  --s5: #e87ba4; --s6: #008300; --s7: #4a3aa7; --s8: #e34948;
  --div-neg: #184f95; --div-mid: #f0efec; --div-pos: #b3261e; --nodata: #e1e0d9;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500;
    --s5: #d55181; --s6: #008300; --s7: #9085e9; --s8: #e66767;
    --div-neg: #3987e5; --div-mid: #383835; --div-pos: #e66767; --nodata: #2c2c2a;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500;
  --s5: #d55181; --s6: #008300; --s7: #9085e9; --s8: #e66767;
  --div-neg: #3987e5; --div-mid: #383835; --div-pos: #e66767; --nodata: #2c2c2a;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; font-weight: 600; }
h2 { font-size: 17px; margin: 36px 0 4px; font-weight: 600; }
p.sub, .sub { color: var(--ink-2); margin: 0 0 14px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
.grid { display: grid; gap: 12px; }
.tiles { grid-template-columns: repeat(auto-fill, minmax(min(100%, 290px), 1fr)); }
.smalls { grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr)); }
.sections { grid-template-columns: repeat(auto-fill, minmax(min(100%, 200px), 1fr)); }
.ctitle .sub { font-weight: 400; }
.tile .val { font-size: 34px; font-weight: 600; margin: 6px 0 2px; }
.tile .kv { display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; color: var(--ink-2);
  font-variant-numeric: tabular-nums; }
.tile .kv b { color: var(--ink); font-weight: 600; }
.key { display: inline-flex; align-items: center; gap: 8px; color: var(--ink); }
.key i { display: inline-block; width: 16px; height: 2px; border-radius: 1px; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 8px 0 12px; }
.ctitle { font-weight: 600; margin-bottom: 4px; }
svg { display: block; width: 100%; height: auto; overflow: visible; }
svg text { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 5px 8px; border-bottom: 1px solid var(--grid); text-align: right; white-space: nowrap; }
th { color: var(--ink-2); font-weight: 600; }
th:first-child, td:first-child, td.l, th.l { text-align: left; }
td.ip { color: var(--muted); }
#tip { position: fixed; pointer-events: none; z-index: 10; background: var(--surface); color: var(--ink);
  border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; box-shadow: 0 4px 16px rgba(0,0,0,.12);
  font-size: 12px; display: none; min-width: 140px; }
#tip .t { color: var(--ink-2); margin-bottom: 4px; }
#tip .r { display: flex; align-items: center; gap: 8px; }
#tip .r i { width: 12px; height: 2px; display: inline-block; }
#tip .r b { font-variant-numeric: tabular-nums; }
#tip .r span { color: var(--ink-2); }
.scale { display: flex; align-items: center; gap: 8px; color: var(--ink-2); font-size: 12px; margin: 6px 0 10px; }
.scale .bar { width: 220px; max-width: 50vw; height: 10px; border-radius: 3px; }
.method { color: var(--ink-2); }
.method code { color: var(--ink); }
.hit { cursor: crosshair; }
</style>
</head>
<body>
<main>
  <h1 id="title"></h1>
  <p class="sub" id="subtitle"></p>
  <div class="legend" id="legend"></div>

  <h2>Ähnlichkeit zur Referenz</h2>
  <p class="sub">Gesamtwert aus Wandstärke und Form (Innenumfang), im gemeinsamen Höhenbereich.</p>
  <div class="grid tiles" id="tiles"></div>

  <div id="matrixBlock">
    <h2>Ähnlichkeitsmatrix</h2>
    <p class="sub">Jeder Liner gegen jeden – Gesamt-Ähnlichkeit in %.</p>
    <div class="card scroll"><div id="matrix"></div></div>
  </div>

  <h2>Wandstärkenverlauf</h2>
  <p class="sub">Wandstärke über der Höhe von distal, je Messrichtung. Punkte = gemessene Werte, Linien = Interpolation.</p>
  <div class="grid smalls" id="profiles"></div>

  <h2>Querschnitte</h2>
  <p class="sub">Blick von proximal: anterior oben, posterior unten, medial rechts, lateral links. Fläche = Liner-Wand.</p>
  <div class="scale"><label><input type="checkbox" id="exag" checked> Wandstärke 4-fach überhöht (Innenkontur maßstäblich)</label></div>
  <div class="grid sections" id="sections"></div>

  <div id="diffBlock">
    <h2>Abweichung zur Referenz</h2>
    <p class="sub">Abgewickelte Liner-Wand: Spalten = Umfangsrichtung, Zeilen = Höhe (oben proximal). Blau = dünner als Referenz, rot = dicker.</p>
    <div class="scale"><span id="scaleMin"></span><div class="bar" id="scaleBar"></div><span id="scaleMax"></span></div>
    <div class="grid smalls" id="diffs"></div>
  </div>

  <h2>Messwerte</h2>
  <p class="sub">Richtung: A/M/P/L oder Winkel ab anterior Richtung medial. Grau = interpoliert (an dieser Stelle beim jeweiligen Liner nicht gemessen), – = außerhalb des Messbereichs. Bei mehr als 16 Messpunkten pro Höhe zeigt die Tabelle ein 22,5°-Raster.</p>
  <div class="card scroll"><table id="points"></table></div>

  <h2>Stammdaten</h2>
  <div class="card scroll"><table id="meta"></table></div>

  <h2>Berechnung</h2>
  <div class="method" id="method"></div>
</main>
<div id="tip"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const NS = "http://www.w3.org/2000/svg";
const fmt = (v, d = 1) => v == null ? "–" : v.toFixed(d).replace(".", ",");
const sgn = (v, d = 1) => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "±") + fmt(Math.abs(v), d);
const col = i => "var(--s" + (i % 8 + 1) + ")";
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function el(tag, attrs, parent, text) {
  const e = tag.startsWith("svg:") ? document.createElementNS(NS, tag.slice(4)) : document.createElement(tag);
  if (attrs) for (const k in attrs) {
    if (k === "style") Object.assign(e.style, attrs[k]); else e.setAttribute(k, attrs[k]);
  }
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function keyEl(parent, i, label) {
  const k = el("span", { class: "key" }, parent);
  el("i", { style: { background: col(i) } }, k);
  el("span", null, k, label);
  return k;
}

// ---- Tooltip -------------------------------------------------------------
const tip = document.getElementById("tip");
function showTip(ev, title, rows) {
  tip.replaceChildren();
  el("div", { class: "t" }, tip, title);
  for (const r of rows) {
    const d = el("div", { class: "r" }, tip);
    if (r.slot != null) el("i", { style: { background: col(r.slot) } }, d);
    el("b", null, d, r.value);
    el("span", null, d, r.label);
  }
  tip.style.display = "block";
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = ev.clientX + 14, y = ev.clientY + 14;
  if (x + w > innerWidth - 8) x = ev.clientX - w - 14;
  if (y + h > innerHeight - 8) y = ev.clientY - h - 14;
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = Math.max(8, y) + "px";
}
const hideTip = () => { tip.style.display = "none"; };

// ---- Farbe -----------------------------------------------------------------
function hex(h) { h = h.replace("#", ""); return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16)); }
function mix(a, b, u) { const A = hex(a), B = hex(b); return "rgb(" + A.map((v, i) => Math.round(v + (B[i] - v) * u)).join(",") + ")"; }
function divColor(d, lim) {
  if (d == null) return css("--nodata");
  const f = Math.max(-1, Math.min(1, d / lim));
  return mix(css("--div-mid"), css(f > 0 ? "--div-pos" : "--div-neg"), Math.abs(f));
}
function seqColor(f) {
  const s = D.sequenziell, x = Math.max(0, Math.min(1, f)) * (s.length - 1);
  const i = Math.min(Math.floor(x), s.length - 2);
  return mix(s[i], s[i + 1], x - i);
}
function inkOn(rgb) {
  const m = rgb.match(/\d+/g).map(Number);
  const lum = (0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]) / 255;
  return lum > 0.55 ? "#0b0b0b" : "#ffffff";
}
function niceTicks(lo, hi, n) {
  const span = hi - lo || 1, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map(s => s * mag).find(s => span / s <= n) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

// ---- Kopf ------------------------------------------------------------------
document.getElementById("title").textContent = D.titel;
document.getElementById("subtitle").textContent =
  "Referenz: " + D.liner[0].label + " · " + D.liner.length + " Liner · erstellt " + D.erstellt;
const legend = document.getElementById("legend");
D.liner.forEach((l, i) => keyEl(legend, i, l.label + (i === 0 ? " (Referenz)" : "")));

// ---- Kacheln ---------------------------------------------------------------
const tiles = document.getElementById("tiles");
D.paare.forEach((p, n) => {
  const i = n + 1, t = el("div", { class: "card tile" }, tiles);
  keyEl(t, i, D.liner[i].label);
  if (!p) { el("div", { class: "sub" }, t, "Keine gemeinsamen Messhöhen – kein Vergleich möglich."); return; }
  el("div", { class: "val" }, t, fmt(p.aehnlichkeit, 0) + " %");
  const kv = el("div", { class: "kv" }, t);
  const row = (k, v) => { el("span", null, kv, k); el("b", null, kv, v); };
  row("Wandstärke", fmt(p.aehnlichkeit_wand, 0) + " %");
  row("Form (Umfang)", fmt(p.aehnlichkeit_form, 0) + " %");
  row("mittlere |Δ| Wand", fmt(p.mae_mm, 2) + " mm");
  row("mittlere Δ Wand", sgn(p.bias_mm, 2) + " mm");
  row("größte Δ Wand", sgn(p.max_mm, 1) + " mm (" + fmt(p.max_hoehe_cm, 0) + " cm, " + p.max_winkel + ")");
  row("Muster-Korrelation r", p.korrelation == null ? "–" : fmt(p.korrelation, 2));
  row("mittlere |Δ| Umfang", fmt(p.umfang_mae_cm, 1) + " cm");
  row("Δ distal", sgn(p.distal_diff_mm, 1) + " mm");
  row("Vergleichsbereich", fmt(p.bereich_cm[0], 0) + "–" + fmt(p.bereich_cm[1], 0) + " cm");
});

// ---- Matrix ----------------------------------------------------------------
function drawMatrix() {
  const host = document.getElementById("matrix");
  host.replaceChildren();
  if (D.liner.length < 3) { document.getElementById("matrixBlock").style.display = "none"; return; }
  const n = D.liner.length, cell = 56, lab = 26;
  const svg = el("svg:svg", { viewBox: `0 0 ${lab + n * (cell + 2) + 4} ${lab + n * (cell + 2) + 4}`,
    style: { maxWidth: (lab + n * (cell + 2) + 4) + "px" } }, host);
  for (let i = 0; i < n; i++) {
    el("svg:text", { x: lab + i * (cell + 2) + cell / 2, y: lab - 8, "text-anchor": "middle" }, svg, "#" + (i + 1));
    el("svg:text", { x: lab - 8, y: lab + i * (cell + 2) + cell / 2 + 4, "text-anchor": "end" }, svg, "#" + (i + 1));
    for (let j = 0; j < n; j++) {
      const v = D.matrix[i][j], x = lab + j * (cell + 2), y = lab + i * (cell + 2);
      const fill = v == null ? css("--nodata") : seqColor((v - 50) / 50);
      const r = el("svg:rect", { x, y, width: cell, height: cell, rx: 4, fill, class: "hit" }, svg);
      const tx = el("svg:text", { x: x + cell / 2, y: y + cell / 2 + 4, "text-anchor": "middle",
        style: { fill: v == null ? "var(--muted)" : inkOn(fill), fontSize: "12px", pointerEvents: "none" } }, svg, v == null ? "–" : fmt(v, 0));
      r.addEventListener("pointermove", ev => showTip(ev, "Ähnlichkeit", [
        { slot: i, value: "#" + (i + 1), label: D.liner[i].label },
        { slot: j, value: "#" + (j + 1), label: D.liner[j].label },
        { value: v == null ? "–" : fmt(v, 1) + " %", label: "gesamt" }]));
      r.addEventListener("pointerleave", hideTip);
    }
  }
  const k = el("div", { class: "legend" }, host);
  D.liner.forEach((l, i) => el("span", { class: "sub" }, k, "#" + (i + 1) + " " + l.label));
}

// ---- Profile ---------------------------------------------------------------
function drawProfiles() {
  const host = document.getElementById("profiles");
  host.replaceChildren();
  let tMax = 0, hMax = 0;
  for (const l of D.liner) {
    for (const s of l.profil.t) for (const v of s) tMax = Math.max(tMax, v);
    hMax = Math.max(hMax, l.profil.h[l.profil.h.length - 1]);
  }
  const yt = niceTicks(0, tMax * 1.08, 5), yMax = yt[yt.length - 1];
  const xt = niceTicks(0, hMax, 6), xMax = Math.max(xt[xt.length - 1], hMax);
  const W = 320, H = 210, m = { l: 30, r: 10, t: 8, b: 22 };
  const X = h => m.l + h / xMax * (W - m.l - m.r), Y = t => H - m.b - t / yMax * (H - m.t - m.b);
  for (let k = 0; k < 4; k++) {
    const card = el("div", { class: "card" }, host);
    const ct = el("div", { class: "ctitle" }, card, D.seiten[k] + " (" + ["0°", "90°", "180°", "270°"][k] + ")");
    el("span", { class: "sub" }, ct, " · Wand mm über Höhe cm");
    const svg = el("svg:svg", { viewBox: `0 0 ${W} ${H}` }, card);
    for (const v of yt) {
      el("svg:line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: css(v === 0 ? "--axis" : "--grid"), "stroke-width": 1 }, svg);
      el("svg:text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, svg, fmt(v, v % 1 ? 1 : 0));
    }
    for (const v of xt) el("svg:text", { x: X(v), y: H - m.b + 16, "text-anchor": "middle" }, svg, fmt(v, 0));
    D.liner.forEach((l, i) => {
      const d = l.profil.h.map((h, n) => (n ? "L" : "M") + X(h).toFixed(1) + " " + Y(l.profil.t[k][n]).toFixed(1)).join(" ");
      el("svg:path", { d, fill: "none", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round", style: { stroke: col(i) } }, svg);
    });
    D.liner.forEach((l, i) => l.mess[k].forEach(g => el("svg:circle", { cx: X(g.h), cy: Y(g.v), r: 4,
      "stroke-width": 2, style: { fill: col(i), stroke: "var(--surface)" } }, svg)));
    const cross = el("svg:line", { y1: m.t, y2: H - m.b, stroke: css("--axis"), "stroke-width": 1, visibility: "hidden" }, svg);
    const hit = el("svg:rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent", class: "hit" }, svg);
    hit.addEventListener("pointermove", ev => {
      const box = svg.getBoundingClientRect(), px = (ev.clientX - box.left) / box.width * W;
      const h = Math.max(0, Math.min(xMax, (px - m.l) / (W - m.l - m.r) * xMax));
      const hs = Math.round(h * 2) / 2;
      cross.setAttribute("x1", X(hs)); cross.setAttribute("x2", X(hs)); cross.setAttribute("visibility", "visible");
      const rows = D.liner.map((l, i) => {
        const n = l.profil.h.findIndex(v => Math.abs(v - hs) < 1e-6);
        const meas = l.mess[k].some(g => Math.abs(g.h - hs) < 1e-6);
        return { slot: i, value: n < 0 ? "–" : fmt(l.profil.t[k][n], 1) + " mm", label: l.label + (meas ? " · gemessen" : "") };
      });
      showTip(ev, D.seiten[k] + " · " + fmt(hs, 1) + " cm", rows);
    });
    hit.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
  }
}

// ---- Querschnitte ----------------------------------------------------------
function drawSections() {
  const host = document.getElementById("sections");
  host.replaceChildren();
  const ex = document.getElementById("exag").checked ? 4 : 1;
  let rMax = 0;
  for (const s of D.querschnitte) for (const q of s.liner) if (q) rMax = Math.max(rMax, q.ri + ex * Math.max(...q.t));
  rMax *= 1.08;
  const S = 220, c = S / 2, k = (S / 2 - 16) / rMax;
  const n = D.querschnitte.length ? D.querschnitte[0].liner.find(Boolean).t.length : 72;
  // anatomischer Winkel: 0 = anterior (oben), 90 = medial (rechts)
  const pt = (r, a) => [c + Math.sin(a) * r * k, c - Math.cos(a) * r * k];
  for (const s of D.querschnitte) {
    const card = el("div", { class: "card" }, host);
    el("div", { class: "ctitle" }, card, fmt(s.h, 0) + " cm");
    const svg = el("svg:svg", { viewBox: `0 0 ${S} ${S}` }, card);
    el("svg:line", { x1: c, x2: c, y1: 12, y2: S - 12, stroke: css("--grid"), "stroke-width": 1 }, svg);
    el("svg:line", { y1: c, y2: c, x1: 12, x2: S - 12, stroke: css("--grid"), "stroke-width": 1 }, svg);
    el("svg:text", { x: c, y: 9, "text-anchor": "middle" }, svg, "A");
    el("svg:text", { x: c, y: S, "text-anchor": "middle" }, svg, "P");
    el("svg:text", { x: S - 2, y: c + 4, "text-anchor": "end" }, svg, "M");
    el("svg:text", { x: 2, y: c + 4, "text-anchor": "start" }, svg, "L");
    s.liner.forEach((q, i) => {
      if (!q) return;
      const outer = [], inner = [];
      for (let j = 0; j < n; j++) {
        const a = 2 * Math.PI * j / n;
        outer.push(pt(q.ri + ex * q.t[j], a)); inner.push(pt(q.ri, a));
      }
      const path = ps => ps.map((p, j) => (j ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ") + " Z";
      el("svg:path", { d: path(outer) + " " + path(inner.slice().reverse()), "fill-rule": "evenodd",
        style: { fill: col(i), fillOpacity: 0.12 } }, svg);
      el("svg:path", { d: path(outer), fill: "none", "stroke-width": 2, style: { stroke: col(i) } }, svg);
      el("svg:path", { d: path(inner), fill: "none", "stroke-width": 1, style: { stroke: col(i), strokeOpacity: 0.7 } }, svg);
    });
    const hit = el("svg:rect", { x: 0, y: 0, width: S, height: S, fill: "transparent", class: "hit" }, svg);
    hit.addEventListener("pointermove", ev => {
      const rows = [];
      s.liner.forEach((q, i) => {
        if (!q) { rows.push({ slot: i, value: "–", label: D.liner[i].label }); return; }
        rows.push({ slot: i, value: q.seiten.map(v => fmt(v, 1)).join(" / ") + " mm",
          label: D.liner[i].label + " · Umfang innen " + fmt(q.umfang, 1) + " cm" });
      });
      showTip(ev, fmt(s.h, 0) + " cm · Wand A / M / P / L", rows);
    });
    hit.addEventListener("pointerleave", hideTip);
  }
}

// ---- Differenzkarten -------------------------------------------------------
function drawDiffs() {
  const host = document.getElementById("diffs");
  host.replaceChildren();
  if (!D.differenzen.length) { document.getElementById("diffBlock").style.display = "none"; return; }
  const lim = D.diff_limit;
  document.getElementById("scaleMin").textContent = "−" + fmt(lim, 1) + " mm";
  document.getElementById("scaleMax").textContent = "+" + fmt(lim, 1) + " mm";
  document.getElementById("scaleBar").style.background =
    `linear-gradient(90deg, ${css("--div-neg")}, ${css("--div-mid")}, ${css("--div-pos")})`;
  D.differenzen.forEach((d, n) => {
    const i = n + 1, card = el("div", { class: "card" }, host);
    const t = el("div", { class: "ctitle" }, card);
    keyEl(t, i, D.liner[i].label + " − Referenz");
    if (!d) { el("div", { class: "sub" }, card, "Kein gemeinsamer Höhenbereich."); return; }
    const nw = D.n_winkel, rows = d.h.length, cw = 16, ch = Math.max(8, Math.min(18, 260 / rows)), gap = 1;
    const ml = 38, mt = 4, mb = 20;
    const W = ml + (nw + 1) * (cw + gap), H = mt + rows * (ch + gap) + mb;
    const svg = el("svg:svg", { viewBox: `0 0 ${W} ${H}` }, card);
    for (let r = 0; r < rows; r++) {
      const ri = rows - 1 - r, y = mt + r * (ch + gap);
      if (rows <= 12 || ri % Math.ceil(rows / 10) === 0 || ri === rows - 1)
        el("svg:text", { x: ml - 6, y: y + ch / 2 + 4, "text-anchor": "end" }, svg, fmt(d.h[ri], 0));
      for (let j = 0; j <= nw; j++) {
        const jj = j % nw, v = d.werte[ri][jj], x = ml + j * (cw + gap);
        const rect = el("svg:rect", { x, y, width: cw, height: ch, rx: 2, fill: divColor(v[0], lim), class: "hit" }, svg);
        rect.addEventListener("pointermove", ev => showTip(ev, fmt(d.h[ri], 0) + " cm · " + D.winkel[jj], [
          { value: sgn(v[0], 2) + " mm", label: "Differenz" },
          { slot: 0, value: fmt(v[1], 2) + " mm", label: D.liner[0].label },
          { slot: i, value: fmt(v[2], 2) + " mm", label: D.liner[i].label }]));
        rect.addEventListener("pointerleave", hideTip);
      }
    }
    ["A", "M", "P", "L", "A"].forEach((a, q) =>
      el("svg:text", { x: ml + q * (nw / 4) * (cw + gap) + cw / 2, y: H - 4, "text-anchor": "middle" }, svg, a));
    el("svg:text", { x: 0, y: mt + 8, "text-anchor": "start" }, svg, "cm");
  });
}

// ---- Tabellen --------------------------------------------------------------
function drawTables() {
  const t = document.getElementById("points");
  t.replaceChildren();
  const head = el("tr", null, el("thead", null, t));
  el("th", null, head, "Höhe");
  el("th", { class: "l" }, head, "Richtung");
  D.liner.forEach((l, i) => { const th = el("th", null, head); keyEl(th, i, "#" + (i + 1) + " mm"); });
  D.liner.slice(1).forEach((l, n) => el("th", null, head, "Δ #" + (n + 2) + " mm"));
  D.liner.slice(1).forEach((l, n) => el("th", null, head, "Δ #" + (n + 2) + " %"));
  const body = el("tbody", null, t);
  let lastH = null;
  for (const p of D.punkte) {
    const tr = el("tr", null, body);
    el("td", null, tr, p.h !== lastH ? fmt(p.h, 1).replace(",0", "") + " cm" : "");
    lastH = p.h;
    el("td", { class: "l" }, tr, p.w);
    p.v.forEach((v, i) => el("td", { class: p.gem[i] ? "" : "ip" }, tr, fmt(v, 1)));
    const ref = p.v[0];
    p.v.slice(1).forEach(v => el("td", null, tr, ref == null || v == null ? "–" : sgn(v - ref, 1)));
    p.v.slice(1).forEach(v => el("td", null, tr, ref == null || v == null ? "–" : sgn((v - ref) / ref * 100, 0)));
  }
  const m = document.getElementById("meta");
  m.replaceChildren();
  const cols = [["#", (l, i) => "#" + (i + 1)], ["Hersteller", l => l.meta.hersteller], ["Artikel", l => l.meta.artikel],
    ["Größe", l => l.meta.groesse], ["Form", l => l.meta.form], ["Material", l => l.meta.material],
    ["Nenn-Wand", l => l.nenn ? fmt(l.nenn, 1) + " mm" : "–"], ["distal", l => fmt(l.distal, 1) + " mm"],
    ["Länge", l => l.laenge ? fmt(l.laenge, 1) + " cm" : "–"], ["Punkte/Höhe", l => l.punkte_je_hoehe],
    ["Seite", l => l.seite], ["Umfang", l => l.bezug],
    ["Notiz", l => l.meta.notiz]];
  const hr = el("tr", null, el("thead", null, m));
  cols.forEach(([h], n) => el("th", { class: n > 5 && n < 9 ? "" : "l" }, hr, h));
  const mb = el("tbody", null, m);
  D.liner.forEach((l, i) => {
    const tr = el("tr", null, mb);
    cols.forEach(([, f], n) => {
      const td = el("td", { class: n > 5 && n < 9 ? "" : "l" }, tr);
      if (n === 0) keyEl(td, i, f(l, i)); else td.textContent = f(l, i) || "–";
    });
  });
}

// ---- Methodik --------------------------------------------------------------
(function () {
  const m = document.getElementById("method"), w = Math.round(D.gewicht_wand * 100);
  const p = t => el("p", null, m, t);
  p("Alle Liner werden im anatomischen Bezugssystem (anterior, medial, posterior, lateral) verglichen – unabhängig von der erfassten Seite. Über den Umfang wird zwischen den Messpunkten monoton kubisch interpoliert (exakt durch jeden Messpunkt, ohne Überschwingen; ein einzelner Punkt gilt rundum), zwischen den Höhen ebenfalls monoton kubisch. Verglichen wird nur im gemeinsamen Höhenbereich, auf einem Raster von 1 cm × 22,5°.");
  p("Ähnlichkeit Wand = 100 % × (1 − mittlere |Δ Wandstärke| ÷ mittlere Wandstärke beider Liner).");
  p("Ähnlichkeit Form = 100 % × (1 − mittlere |Δ Innenumfang| ÷ mittlerer Innenumfang beider Liner).");
  p("Gesamt = " + w + " % Wand + " + (100 - w) + " % Form. Die Muster-Korrelation r (−1…1) zeigt, ob die Wandstärken gleich verteilt sind (z. B. beide posterior dicker) – unabhängig vom absoluten Niveau.");
})();

function drawAll() { drawMatrix(); drawProfiles(); drawSections(); drawDiffs(); drawTables(); }
drawAll();
document.getElementById("exag").addEventListener("change", drawSections);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawAll);
new MutationObserver(drawAll).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
</script>
</body>
</html>
"""
