// Admin "Show report" card: pick a show day, a time range and the sections, then open the report in a
// new tab or download it as a ZIP. The report is made by the server from what is on disk; nothing here
// changes any data. Text is only ever set as text (SW.h).
"use strict";
SW.reportCard = (function () {
  const KEY = "stagewatch.report";
  const SECTIONS = [["summary", "Summary"], ["env", "Environment"], ["wind", "Wind"], ["spl", "Sound level"],
    ["power", "Power"], ["alarms", "Alarms"], ["devices", "Device outages"], ["markers", "Markers"],
    ["schedule", "Running order"], ["system", "System events"]];
  const SHARE_OFF = ["system", "schedule"];     // never in a Sharing excerpt (the server refuses them)
  const PRESETS = [["day", "Full day"], ["show_time", "Show time (doors to curfew)"], ["markers", "Between two markers"], ["custom", "Choose times"]];
  const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

  // What the last choice was, per browser. Reading or writing can fail (private window): then it is
  // simply not remembered.
  function load() {
    try {
      const o = JSON.parse(localStorage.getItem(KEY) || "{}");
      return o && typeof o === "object" ? o : {};
    } catch (_) { return {}; }
  }
  function save(st) {
    try { localStorage.setItem(KEY, JSON.stringify({ preset: st.preset, from: st.from, to: st.to, share: st.share, sections: st.sections })); }
    catch (_) { /* not remembered */ }
  }
  const mem = load();
  const st = {
    show: 0,
    preset: PRESETS.some((p) => p[0] === mem.preset) ? mem.preset : "day",
    from: HHMM.test(mem.from || "") ? mem.from : "19:30",
    to: HHMM.test(mem.to || "") ? mem.to : "23:00",
    share: mem.share === true,
    sections: Array.isArray(mem.sections) ? mem.sections.filter((k) => SECTIONS.some((s) => s[0] === k)) : SECTIONS.map((s) => s[0]),
    fromMarker: 0, toMarker: 0, markers: [], markersFor: 0,
  };

  // The address for the server. Returns { url } or { error } (plain wording).
  function query(extra) {
    if (!st.show) return { error: "Pick a show day first." };
    const p = new URLSearchParams({ show_id: String(st.show), preset: st.preset });
    if (st.preset === "custom") {
      if (!HHMM.test(st.from) || !HHMM.test(st.to)) return { error: "Type the times as 24-hour HH:MM, for example 19:30." };
      p.set("from", st.from); p.set("to", st.to);
    }
    if (st.preset === "markers") {
      if (!st.fromMarker || !st.toMarker || st.fromMarker === st.toMarker) return { error: "Pick two different markers." };
      p.set("from_marker", String(st.fromMarker)); p.set("to_marker", String(st.toMarker));
    }
    const sent = st.share ? st.sections.filter((k) => !SHARE_OFF.includes(k)) : st.sections;
    if (!sent.length) return { error: "Tick at least one section." };
    p.set("sections", sent.join(","));
    if (st.share) p.set("share", "true");
    Object.entries(extra || {}).forEach(([k, v]) => p.set(k, v));
    return { params: p };
  }

  function card(admin) {
    const h = SW.h, toast = SW.toast;
    const shows = (admin.events || []).flatMap((e) => e.shows.map((s) => ({ id: s.id, label: `${s.name} · ${SW.fmtDay(s.day)} (${e.name})` })));
    if (!shows.some((s) => s.id === st.show)) st.show = admin.show ? admin.show.id : (shows[0] ? shows[0].id : 0);
    const current = admin.show ? admin.show.id : 0;

    const showSel = h("select", { class: "touch", "aria-label": "Show day" },
      shows.map((s) => h("option", { value: String(s.id) }, s.id === current ? `${s.label} - so far` : s.label)));
    showSel.value = String(st.show);
    const presetSel = h("select", { class: "touch", "aria-label": "Time range" }, PRESETS.map((p) => h("option", { value: p[0] }, p[1])));
    presetSel.value = st.preset;
    const fromIn = h("input", { type: "text", class: "touch", value: st.from, placeholder: "19:30", inputmode: "numeric", maxlength: "5", "aria-label": "From (HH:MM)" });
    const toIn = h("input", { type: "text", class: "touch", value: st.to, placeholder: "23:00", inputmode: "numeric", maxlength: "5", "aria-label": "To (HH:MM)" });
    const mFrom = h("select", { class: "touch", "aria-label": "First marker" });
    const mTo = h("select", { class: "touch", "aria-label": "Second marker" });
    const timesRow = h("div", { class: "row" }, h("label", { class: "field" }, "From", fromIn), h("label", { class: "field" }, "To", toIn),
      h("p", { class: "muted hint" }, "24-hour site time. Times before 06:00 count as the next morning. A range can be at most 24 hours."));
    const markersRow = h("div", { class: "row" }, h("label", { class: "field" }, "From marker", mFrom), h("label", { class: "field" }, "To marker", mTo));
    const showTimeNote = h("p", { class: "muted hint" }, "Uses the doors and curfew times in the running order.");

    const fillMarkers = () => {
      const opts = (sel, cur) => {
        sel.replaceChildren(h("option", { value: "0" }, "Choose a marker"));
        st.markers.forEach((m) => sel.append(h("option", { value: String(m.id) }, `${SW.fmtTime(m.ts)}  ${m.label}`)));
        sel.value = String(cur);
        if (sel.value !== String(cur)) sel.value = "0";
      };
      opts(mFrom, st.fromMarker); opts(mTo, st.toMarker);
    };
    const loadMarkers = async () => {
      if (!st.show || st.markersFor === st.show) { fillMarkers(); return; }
      try {
        const r = await SW.api("GET", `/api/admin/report/markers?show_id=${encodeURIComponent(st.show)}`);
        st.markers = r.markers || []; st.markersFor = st.show; st.fromMarker = 0; st.toMarker = 0;
      } catch (err) { toast(err.message, true); st.markers = []; }
      fillMarkers();
    };
    const sync = () => {
      timesRow.hidden = st.preset !== "custom";
      markersRow.hidden = st.preset !== "markers";
      showTimeNote.hidden = st.preset !== "show_time";
      if (st.preset === "markers") loadMarkers();
    };

    const boxes = {};
    const sectionBoxes = SECTIONS.map(([key, label]) => {
      const cb = h("input", { type: "checkbox", checked: st.sections.includes(key), "aria-label": label });
      boxes[key] = cb;
      cb.addEventListener("change", () => { st.sections = SECTIONS.map((s) => s[0]).filter((k) => boxes[k].checked); save(st); });
      return h("label", { class: "field inline" }, cb, " ", label);
    });
    const shareBox = h("input", { type: "checkbox", checked: st.share });
    shareBox.addEventListener("change", () => {
      st.share = shareBox.checked;
      if (st.share) SHARE_OFF.forEach((k) => { boxes[k].checked = false; });
      SHARE_OFF.forEach((k) => { boxes[k].disabled = st.share; });
      st.sections = SECTIONS.map((s) => s[0]).filter((k) => boxes[k].checked);
      save(st);
    });

    SHARE_OFF.forEach((k) => { boxes[k].disabled = st.share; });
    showSel.addEventListener("change", () => { st.show = Number(showSel.value); st.markersFor = 0; sync(); });
    presetSel.addEventListener("change", () => { st.preset = presetSel.value; save(st); sync(); });
    fromIn.addEventListener("input", () => { st.from = fromIn.value.trim(); save(st); });
    toIn.addEventListener("input", () => { st.to = toIn.value.trim(); save(st); });
    mFrom.addEventListener("change", () => { st.fromMarker = Number(mFrom.value); });
    mTo.addEventListener("change", () => { st.toMarker = Number(mTo.value); });

    const open = () => {
      const q = query();
      if (q.error) { toast(q.error, true); return; }
      window.open(`/api/admin/report?${q.params.toString()}`, "_blank", "noopener");
    };
    const download = () => {
      const q = query();
      if (q.error) { toast(q.error, true); return; }
      const a = h("a", { href: `/api/admin/report/data.zip?${q.params.toString()}`, download: "", rel: "noopener" });
      document.body.append(a); a.click(); a.remove();
    };

    const demo = admin.emulate ? h("div", { class: "row" },
      h("button", { type: "button", class: "touch", onclick: async () => {
        try { const r = await SW.api("POST", "/api/admin/report/demo"); st.show = r.show_id; toast("Demo day added. Choose it under Show day."); }
        catch (err) { toast(err.message, true); }
      } }, "Add a demo day to report on")) : null;

    sync();
    return SW.card("Show report",
      h("p", { class: "muted hint" }, "A printable summary of a show day: temperatures, alarms, markers and any time Stagewatch or a node was not recording. It only reads what is saved. To save it as a PDF, open it and press Ctrl+P."),
      h("div", { class: "row" }, h("label", { class: "field" }, "Show day", showSel), h("label", { class: "field" }, "Time range", presetSel)),
      timesRow, markersRow, showTimeNote,
      h("fieldset", { style: "border:0;padding:0;margin:8px 0" }, h("legend", { class: "muted" }, "Include"), h("div", { class: "row" }, sectionBoxes),
        h("p", { class: "muted hint" }, "Times when Stagewatch was not recording are always shown.")),
      h("label", { class: "field inline" }, shareBox, " Sharing excerpt (leaves out who acknowledged alarms, marker sources, device fault detail, system events and the running order)"),
      h("div", { class: "row" },
        h("button", { type: "button", class: "primary touch", onclick: open }, "Open report"),
        h("button", { type: "button", class: "touch", onclick: download }, "Download ZIP")),
      demo);
  }
  card.query = query;
  card.state = st;
  return card;
})();
