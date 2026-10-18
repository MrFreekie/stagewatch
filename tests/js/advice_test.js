// Node test for the FOH advice cards (advice.js): the words and rows (pure) and that the cards
// update the same elements in place. Hand-made snapshots (synthetic), no real site data.
// Run: node tests/js/advice_test.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");

class FakeEl {
  constructor(tag) { this.tagName = tag; this.attrs = {}; this.children = []; this._text = ""; this.writes = 0; this.style = {}; this.dataset = {}; this.hidden = false; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  get className() { return this.attrs.class || ""; }
  set className(v) { this.attrs.class = String(v); this.writes++; }
  addEventListener() {}
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); this.writes++; }
  append(...nodes) { for (const n of nodes) { if (n instanceof FakeEl) this.children.push(n); else this._text += String(n); } }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = []; this._text = String(v); this.writes++; }
}
const ctx = { Intl, Date, Number, Math, Object, Array, String, Infinity, JSON, console, isFinite, window: {}, Node: FakeEl, Element: FakeEl };
ctx.document = { createElement: (t) => new FakeEl(t), createElementNS: (ns, t) => new FakeEl(t), createTextNode: (s) => String(s) };
vm.createContext(ctx);
for (const f of ["common.js", "advice.js"]) vm.runInContext(fs.readFileSync(path.join(STATIC, f), "utf8"), ctx, { filename: f });
const SW = vm.runInContext("SW", ctx);
const A = SW.advice;
SW.setSiteTime({ timezone: "", utc_offset_s: 0 });

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
function ok(cond, what) { count++; if (!cond) { fails++; console.log(`FAIL ${what}`); } }

// ---- the words for a change in dB: 0.5 and 2 dB are the cut-offs; positive = more top end lost
const w = (c) => A.changeWords(c);
eq([w(0).text, w(0.49).text, w(-0.49).text], ["about the same", "about the same", "about the same"], "under 0.5 dB is about the same");
eq(w(0.5).text, "slightly more top end lost", "0.5 dB is the first step");
eq(w(-1.99).text, "slightly less top end lost", "just under 2 dB is still slightly");
eq([w(2).text, w(-2.5).text], ["clearly more top end lost", "clearly less top end lost"], "2 dB and over is clearly");
eq([w(-1).sym, w(1).sym, w(0.1).sym, w(null).sym], ["▲", "▼", "≈", ""], "symbols: up = less lost, down = more lost");
eq(w(null).text, "—", "unknown change is a dash, not zero");
eq(w(NaN).text, "—", "NaN is a dash");
eq(A.signedDb(-0.96), "-1.0 dB", "one decimal, signed");
eq(A.signedDb(0.04), "0.0 dB", "a tiny value is not shown with a sign");

// ---- drift rows and header
const T0 = Date.UTC(2026, 9, 18, 14, 5) / 1000;
const path1 = (o) => Object.assign({ id: "tower", name: "Delay tower L", distance_m: 85, drift_ms: -1.892, suggested_delay_ms: 10.608, current_delay_ms: 12.5,
  threshold_ms: 1, level: "advisory", flags: [], source: "site", source_fallback: [], stale: false, phase_deg: null, phase_hint_hz: null }, o || {});
const ADV = {
  resolution: "octave",
  drift_ref: { label: "Aligned", ts: T0, marker_id: 4, has_readings: true, was: { t_c: 20, rh: 50, p_pa: 101300 }, delta: { t_c: 6.2, rh: 12, p_pa: -50 } },
  air_ref: { label: "Soundcheck", ts: T0, marker_id: 3, has_readings: true, was: { t_c: 25, rh: 40, p_pa: 101300 }, delta: { t_c: -10, rh: 34.3, p_pa: 0 } },
  paths: [path1(), path1({ id: "fill", name: "Front fill", distance_m: 8, drift_ms: 0.004, suggested_delay_ms: 3.004, current_delay_ms: 3, level: "ok" }),
    path1({ id: "sub", name: "Sub to tops", distance_m: 2.5, drift_ms: null, suggested_delay_ms: null, current_delay_ms: null, level: "ok" })],
  positions: [],
};
eq(A.driftHeader(ADV.drift_ref), "Since Aligned at 14:05 · +6.2 °C · +12 % RH", "drift header");
eq(A.driftHeader(null), A.NO_DRIFT_MARKER, "no marker today");
eq(A.driftHeader({ label: "Aligned", ts: T0, has_readings: false, delta: {} }), "No readings recorded at Aligned (14:05)", "marker without readings");
let dv = A.driftView(ADV, "tablet", "");
eq(dv.rows.map((r) => r.cells.drift), ["-1.89 ms", "0.00 ms", "—"], "signed two decimals, unknown is a dash");
eq(dv.rows.map((r) => r.cells.status), ["▲ CHECK", "OK", "—"], "status words");
eq(dv.rows[0].cells.suggested, "10.61 ms", "suggested delay");
eq(dv.rows[2].cells.suggested, "—", "no current delay, no suggestion");
eq([dv.rows[0].cells.distance, dv.rows[2].cells.distance], ["85 m", "2.5 m"], "distance without a needless decimal");
eq(dv.footer, "Advice only. Stagewatch never changes your delays.", "footer");
dv = A.driftView(ADV, "wall", "");
eq(dv.rows.map((r) => r.id), ["tower", "fill", "sub"], "wall: largest drift first, unknown last");
const many = Object.assign({}, ADV, { paths: [path1({ id: "a", drift_ms: 0.1 }), path1({ id: "b", drift_ms: -0.9 }), path1({ id: "c", drift_ms: 0.5 }), path1({ id: "d", drift_ms: -0.2 })] });
eq(A.driftView(many, "wall", "").rows.map((r) => r.id), ["b", "c", "d"], "wall shows the 3 largest by size, either direction");
eq(A.driftView(many, "phone", "").rows.map((r) => r.id), ["a", "b", "c", "d"], "phone: every path, in order");
eq(A.driftView(ADV, "tablet", "3m ago").notes[0], "Old readings: the last one was 3m ago", "stale readings say how old");
eq(A.driftNotes(path1({ flags: ["out_of_range", "humidity_assumed"], source_fallback: ["rh"] })),
  [A.OUT_OF_RANGE, "No humidity reading: 50 % assumed", "Site average used"], "drift notes");

// ---- air loss
const band = (b, now, ch) => ({ band: b, hz_used: 1, now_db: now, change_db: ch });
const POS = (id, name, d, bands, other, flags) => ({ id, name, distance_m: d, bands, other_bands: other || [], flags: flags || [], stale: false });
const OCT = (c8) => [band("4k", 1.5, c8 / 4), band("8k", 4.7, c8), band("16k", 12.4, c8 * 3)];
ADV.positions = [POS("foh", "FOH", 40, OCT(-1.0), [band("4k", 1.5, -0.2), band("5k", 2, -0.3), band("8k", 4.7, -1.0)]),
  POS("rear", "Rear", 90, OCT(-2.3), [])];
let av = A.airView(ADV, "tablet", "octave", "");
eq(av.rows[0].bands.map((b) => b.band), ["4k", "8k", "16k"], "octave bands");
eq(av.rows[0].head.fig, "-1.0 dB", "the 8 kHz figure");
eq(av.rows[0].head.words, "slightly less top end lost", "the 8 kHz words");
eq(av.rows[0].head.now, "now 4.7 dB over 40 m", "small grey line: now X dB over N m");
eq(av.rows[1].head.words, "clearly less top end lost", "2.3 dB is clearly");
eq(A.airView(ADV, "tablet", "third", "").rows[0].bands.map((b) => b.band), ["4k", "5k", "8k"], "the card can ask for the other view");
eq(A.airView(ADV, "tablet", "", "").rows[0].bands.map((b) => b.band), ["4k", "8k", "16k"], "no choice follows the system");
eq(A.airHeader(ADV.air_ref), "Compared with Soundcheck at 14:05: 25.0 → 15.0 °C, 40 → 74 % RH", "air header");
eq(A.airHeader(null), A.NO_AIR_MARKER, "no soundcheck marker");
eq(av.subtitle, "Estimated from air temperature and humidity. Advisory, not a measurement.", "subtitle");
eq(A.airView(ADV, "wall", "", "").rows.map((r) => r.id), ["rear", "foh"], "wall: largest 8 kHz change first");
const noRh = Object.assign({}, ADV, { positions: [POS("foh", "FOH", 40, [band("4k", null, null), band("8k", null, null), band("16k", null, null)], [], ["needs_humidity"])] });
av = A.airView(noRh, "tablet", "", "");
eq(av.notes, ["FOH: Needs a humidity reading"], "no humidity: said in words");
eq(av.rows[0].bands, [], "no humidity: no numbers at all");
const rough = Object.assign({}, ADV, { positions: [POS("foh", "FOH", 40, OCT(1), [], ["out_of_range"])] });
eq(A.airView(rough, "tablet", "", "").notes, ["FOH: Outside the model's range: treat as rough"], "outside the range");
const BANNED = /\b(safe|compliant|certified|accurate|guaranteed)\b/i;
const allText = JSON.stringify([A.AIR_SUBTITLE, A.AIR_FOOTER, A.DRIFT_FOOTER, A.RULE_OF_THUMB, A.NO_DRIFT_MARKER, A.NO_AIR_MARKER, A.OUT_OF_RANGE]);
ok(!BANNED.test(allText), "no banned words in the fixed strings");

// ---- the cards update the same elements in place
for (const layout of ["tablet", "phone", "wall"]) {
  const root = new FakeEl("div");
  const update = A.mountDrift(root);
  update(A.driftView(ADV, layout, ""));
  const before = root.textContent;
  ok(before.indexOf("-1.89 ms") >= 0 && before.indexOf("Advice only") >= 0, `drift ${layout}: text drawn`);
  const first = root.children[1].children[0];
  update(A.driftView(ADV, layout, ""));
  eq(root.children[1].children[0], first, `drift ${layout}: structure kept`);
  let writes = 0; const count0 = (e) => { writes += e.writes; e.children.forEach(count0); }; count0(root);
  update(A.driftView(ADV, layout, ""));
  let after = 0; const count1 = (e) => { after += e.writes; e.children.forEach(count1); }; count1(root);
  eq(after, writes, `drift ${layout}: an unchanged update touches nothing`);
  const moved = JSON.parse(JSON.stringify(ADV)); moved.paths[0].drift_ms = -1.5;
  update(A.driftView(moved, layout, ""));
  ok(root.textContent.indexOf("-1.50 ms") >= 0 && root.textContent.indexOf("-1.89 ms") < 0, `drift ${layout}: a changed value is patched`);
  eq(root.children[1].children[0], first, `drift ${layout}: still the same elements after a change`);
}
for (const layout of ["tablet", "phone", "wall"]) {
  const root = new FakeEl("div");
  const update = A.mountAir(root);
  update(A.airView(ADV, layout, "", ""));
  ok(root.textContent.indexOf("slightly less top end lost") >= 0, `air ${layout}: words drawn`);
  const body = root.children[2], first = body.children[0];
  const moved = JSON.parse(JSON.stringify(ADV)); moved.positions[0].bands[1].change_db = 1.2;
  update(A.airView(moved, layout, "", ""));
  ok(root.textContent.indexOf("+1.2 dB") >= 0 && root.textContent.indexOf("slightly more top end lost") >= 0, `air ${layout}: a changed value is patched`);
  eq(body.children[0], first, `air ${layout}: still the same elements after a change`);
}

if (fails) { console.log(`${fails} of ${count} checks failed`); process.exit(1); }
console.log(`ok: ${count} checks`);
