// OSC actions, shared by the Schedule page and Admin (thresholds, Action targets). Admin only.
// One message editor: target, OSC address and up to 8 typed values. read() gives
// {target_id, address, args:[{t, v}]} or null when the address is left empty.
"use strict";

SW.ACTION_WARNING = "Actions are for show audio and media. Never use them to control rigging, hoists, power or safety systems.";
SW.ACTION_EXAMPLES = "Examples (check your receiver's OSC settings): QLab /go or /cue/1/start (port 53000). Companion /location/1/0/1/press (port 12321).";
SW.ACTION_STATUS = { sent: "sent", failed: "failed", missed: "missed", skipped: "off (not armed)" };

SW.actionMessageEditor = function (msg, targets, opts) {
  const { h } = SW;
  opts = opts || {};
  const m = msg || {};
  const tsel = h("select", { class: "touch", "aria-label": `${opts.label || "Message"}: target` },
    [h("option", { value: "" }, targets.length ? "Choose a target" : "No targets yet")].concat(
      targets.map((t) => h("option", { value: t.id }, `${t.name}${t.enabled === false ? " (off)" : ""}`))));
  tsel.value = m.target_id || "";
  const addr = h("input", { class: "touch", value: m.address || "", placeholder: opts.placeholder || "/go", maxlength: 200,
    autocomplete: "off", spellcheck: "false", "aria-label": `${opts.label || "Message"}: OSC address` });
  const argsBox = h("div", { class: "act-args" });
  const addArg = (a) => {
    a = a || { t: "i", v: "" };
    const t = h("select", { class: "touch", "aria-label": "Value type" },
      [["i", "Whole number"], ["f", "Decimal"], ["s", "Text"]].map(([v, l]) => h("option", { value: v }, l)));
    t.value = a.t || "s";
    const v = h("input", { class: "touch", value: a.v === undefined || a.v === null ? "" : String(a.v), maxlength: 200,
      list: opts.placeholders ? "act-placeholders" : null, "aria-label": "Value" });
    const row = h("span", { class: "act-arg" }, t, v,
      h("button", { type: "button", class: "touch danger", "aria-label": "Remove this value", onclick: () => row.remove() }, "✕"));
    row._read = () => {
      const raw = v.value.trim();
      if (opts.placeholders && /^\{(value|level|label)\}$/.test(raw)) return { t: t.value, v: raw };
      return { t: t.value, v: t.value === "s" ? v.value : raw === "" ? 0 : Number(raw) };
    };
    argsBox.append(row);
  };
  (m.args || []).forEach(addArg);
  const el = h("div", { class: "act-msg" },
    h("label", { class: "field" }, opts.label || "Send", tsel),
    h("label", { class: "field act-addr" }, "OSC address", addr),
    argsBox,
    h("button", { type: "button", class: "touch", onclick: () => { if (argsBox.children.length < 8) addArg(); else SW.toast("Up to 8 values.", true); } }, "Add value"),
    opts.placeholders ? h("datalist", { id: "act-placeholders" }, ["{value}", "{level}", "{label}"].map((x) => h("option", { value: x }))) : null,
    opts.test ? h("button", { type: "button", class: "touch", onclick: () => SW.actionTest(read(), targets) }, "Send test") : null);
  function read() {
    if (!addr.value.trim()) return null;
    return { target_id: tsel.value, address: addr.value.trim(), args: [...argsBox.children].map((r) => r._read()) };
  }
  return { el, read };
};

// Send one message now (works while actions are off), after a confirm that names the target.
SW.actionTest = async function (msg, targets) {
  if (!msg || !msg.target_id) { SW.toast("Choose a target and type an OSC address first.", true); return; }
  const t = targets.find((x) => x.id === msg.target_id) || {};
  if (!confirm(`Send ${msg.address} to ${t.name || "this target"} at ${t.host}:${t.port} now?`)) return;
  try {
    const r = await SW.api("POST", "/api/admin/actions/test", msg);
    SW.toast(r.status === "sent" ? `Test sent to ${t.name}` : `Not sent: ${r.detail}`, r.status !== "sent");
  } catch (err) { SW.toast(err.message, true); }
};

// "Actions ARMED" / "Actions off" with the switch. onChange(status) after a change.
SW.actionsArmedBar = function (status, onChange) {
  const { h } = SW;
  const armed = !!(status && status.armed);
  return h("div", { class: `act-armed ${armed ? "on" : "off"}`, role: "status" },
    h("strong", {}, armed ? "● Actions ARMED" : "○ Actions off"),
    h("span", {}, armed ? " Due actions send at their time." : " Nothing is sent. Due actions are logged as not armed. Actions are off after every start of Stagewatch."),
    h("button", { type: "button", class: armed ? "touch" : "primary touch", onclick: async () => {
      if (!armed && !confirm("Arm actions? Stagewatch will send each action's OSC message at its time until you switch actions off.")) return;
      try {
        const r = await SW.api("PUT", "/api/admin/actions/armed", { armed: !armed });
        SW.toast(r.armed ? "Actions armed" : "Actions off");
        if (onChange) onChange(r);
      } catch (err) { SW.toast(err.message, true); }
    } }, armed ? "Switch actions off" : "Arm actions"));
};

SW.actionLogList = function (log) {
  const { h } = SW;
  const src = { schedule: "Schedule", threshold: "Threshold", test: "Test" };
  if (!log || !log.length) return h("p", { class: "muted" }, "Nothing sent yet in this show.");
  return h("div", { class: "table-scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Time", "From", "What", "Target", "Address", "Result"].map((x) => h("th", {}, x)))),
    h("tbody", {}, log.slice(0, 30).map((r) => h("tr", {},
      h("td", { class: "num" }, SW.fmtTime(r.fired || r.due)), h("td", {}, src[r.source] || r.source), h("td", {}, r.label),
      h("td", {}, r.target || "—"), h("td", {}, r.address || "—"),
      h("td", { class: r.status === "sent" ? "" : "error" }, `${SW.ACTION_STATUS[r.status] || r.status}${r.detail && r.detail !== "emulated" ? `: ${r.detail}` : r.detail === "emulated" ? " (emulated)" : ""}`))))));
};
