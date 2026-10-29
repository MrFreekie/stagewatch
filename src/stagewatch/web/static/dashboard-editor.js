// Edit cards (its own page, /dashboard-editor?d=<url name>): which cards one dashboard shows, in
// what order, each card's options and its stage, with a preview that follows the unsaved changes (posted to the
// preview frame only; dashboard.js ?preview=1 checks them and never saves). It needs
// the admin login; without one the page sends you to Admin and back (Admin accepts only the fixed
// path ?next=/dashboard-editor, so the chosen dashboard waits in sessionStorage). Saving uses the
// same PUT /api/admin/dashboards as Admin: this dashboard's card fields, and the others with their
// base fields only, so the server keeps their cards.
"use strict";

(() => {
  const { h, api, toast } = SW;
  const app = document.getElementById("app");
  const PICK_KEY = "sw.dashboard-editor.slug";
  const BASE = ["slug", "title", "layout", "allow_marker", "allow_ack"];
  const SIZES = { tablet: [1280, 800], phone: [375, 812], wall: [1920, 1080] };
  let admin = null, dash = null, editor = null, dirty = false, onReadyNow = null;

  // The Day / Night choice this browser made on Admin or a dashboard.
  try {
    const t = localStorage.getItem("stagewatch.theme");
    if (t === "day" || t === "night") document.documentElement.setAttribute("data-theme", t === "day" ? "light" : "dark");
  } catch (_) { /* follow the device */ }

  const toLogin = () => { location.replace("/admin?next=/dashboard-editor"); };
  const slugFromUrl = () => { const m = /[?&]d=([^&]*)/.exec(location.search); try { return m ? decodeURIComponent(m[1]) : ""; } catch (_) { return ""; } };
  const remember = (slug) => { try { sessionStorage.setItem(PICK_KEY, slug); } catch (_) { /* ignore */ } };
  const remembered = () => { try { return sessionStorage.getItem(PICK_KEY) || ""; } catch (_) { return ""; } };

  window.addEventListener("beforeunload", (ev) => { if (dirty) { ev.preventDefault(); ev.returnValue = ""; } });

  function preview(d) {
    const size = SIZES[d.layout] || SIZES.tablet;
    // Shown only: no clicks or keyboard reach it, so nothing can be acknowledged or marked from here.
    const frame = h("iframe", { class: "de-frame", src: `/d/${encodeURIComponent(d.slug)}?preview=1`, title: `Preview of ${d.title || d.slug}`,
      tabindex: "-1", "aria-hidden": "true", inert: "", width: String(size[0]), height: String(size[1]) });
    const box = h("div", { class: "de-frame-box" }, frame);
    const fit = () => {
      const w = box.clientWidth || size[0], s = Math.min(1, w / size[0]);
      frame.style.transform = `scale(${s})`;
      box.style.height = `${Math.round(size[1] * s)}px`;
    };
    window.addEventListener("resize", fit);
    requestAnimationFrame(fit);
    // The frame says when it has its snapshot; send the draft again then (first load, reconnect).
    const send = () => {
      if (!frame.contentWindow) return;
      try { frame.contentWindow.postMessage({ type: "sw-preview-draft", draft: dirty && editor ? editor.read() : null }, location.origin); } catch (_) { /* ignore */ }
    };
    const onReady = (ev) => {
      if (ev.origin === location.origin && ev.source === frame.contentWindow && ev.data && ev.data.type === "sw-preview-ready") send();
    };
    if (onReadyNow) window.removeEventListener("message", onReadyNow);
    onReadyNow = onReady;
    window.addEventListener("message", onReady);
    return { el: box, send, reload: () => { frame.src = frame.src; } };
  }

  function render() {
    const slug = dash.slug;
    const pick = h("select", { class: "touch", "aria-label": "Dashboard", onchange: () => {
      if (dirty && !window.confirm("Leave without saving your changes to this dashboard?")) { pick.value = slug; return; }
      dirty = false;
      location.assign(`/dashboard-editor?d=${encodeURIComponent(pick.value)}`);
    } }, admin.config.dashboards.map((d) => h("option", { value: d.slug }, `${d.title || d.slug} (/d/${d.slug}, ${d.layout})`)));
    pick.value = slug;
    const count = h("span", { class: "muted" });
    const tickedText = () => `${editor.count()} cards ticked${editor.count() >= editor.max ? `. Maximum ${editor.max} cards` : ""}`;
    const status = h("p", { class: "muted de-status", role: "status" });
    let view = null;
    const onChange = () => {
      if (editor) { dirty = true; count.textContent = tickedText(); status.textContent = "Unsaved changes"; if (view) view.send(); }
    };
    editor = SW.cardsEditor(dash, onChange, { cards: admin.cards, contactsPinSet: !!(admin.config.contacts && admin.config.contacts.reveal_pin_set) });
    dirty = false;
    count.textContent = tickedText();
    status.textContent = "";
    view = preview(dash);
    const save = h("button", { class: "primary touch", onclick: async () => {
      save.disabled = true;
      try {
        // Re-read the list, so a dashboard added or renamed in Admin meanwhile is kept.
        const fresh = await api("GET", "/api/admin/state");
        const list = fresh.config.dashboards;
        if (!list.some((d) => d.slug === slug)) throw new Error("This dashboard was renamed or removed in Admin. Go back to Admin and open it again.");
        const body = list.map((d) => {
          const base = {};
          BASE.forEach((k) => { if (d[k] !== undefined) base[k] = d[k]; });
          return d.slug === slug ? Object.assign(base, editor.read()) : base;
        });
        const saved = await api("PUT", "/api/admin/dashboards", body);
        admin.config.dashboards = saved;
        dash = saved.find((d) => d.slug === slug) || dash;
        dirty = false;
        status.textContent = "Saved. The preview shows the saved dashboard.";
        view.reload();
      } catch (err) {
        if (err.status === 401) { toLogin(); return; }
        const msg = String(err.message).replace(/^\d+\.\w+:\s*(Value error,\s*)?/, "");   // the server's field path is not for crew
        status.textContent = `Not saved: ${msg}`;
        toast(msg, true);
      } finally { save.disabled = false; }
    } }, "Save");
    const undo = h("button", { class: "touch", type: "button", onclick: () => { render(); } }, "Undo changes");
    const stages = admin.stages || [];
    app.replaceChildren(
      h("datalist", { id: "stage-list" }, stages.map((s) => h("option", { value: s }))),
      h("section", { class: "card de-head" },
        h("h2", {}, "Dashboard"),
        h("div", { class: "row" }, pick, h("a", { href: `/d/${encodeURIComponent(slug)}`, target: "_blank", rel: "noopener" }, "Open it")),
        h("p", { class: "muted" }, "Choose the cards this screen shows, top to bottom. Title, layout and what users may do are set in Admin → User dashboards.")),
      h("div", { class: "de-grid" },
        h("section", { class: "card de-cards" },
          h("h2", {}, h("span", {}, "Cards"), h("span", { class: "grow" }), count),
          editor.el,
          h("div", { class: "row de-actions" }, status, h("span", { class: "grow" }), undo, save)),
        h("section", { class: "card de-preview" },
          h("h2", {}, h("span", {}, "Preview"), h("span", { class: "grow" }), h("span", { class: "muted" }, `${dash.layout}, follows your changes`)),
          view.el,
          h("p", { class: "muted hint" }, "The preview shows your changes straight away, at the layout's screen size, but only here: nothing is saved and other screens keep the saved version until you press Save. Contact phone numbers and the contacts area show only after Save. If the strip in the preview says ‘saved version’, it could not show these changes. It is for looking only: taps do nothing here."))));
  }

  async function start() {
    try {
      admin = await api("GET", "/api/admin/state");
    } catch (err) {
      if (err.status === 401) { remember(slugFromUrl() || remembered()); toLogin(); return; }
      app.replaceChildren(SW.card("Edit cards", h("p", {}, `Could not load the dashboards: ${err.message}`)));
      return;
    }
    const want = slugFromUrl() || remembered();
    const list = admin.config.dashboards || [];
    dash = list.find((d) => d.slug === want) || list[0];
    if (!dash) { app.replaceChildren(SW.card("Edit cards", h("p", {}, "There are no dashboards yet. Add one in Admin → User dashboards."))); return; }
    remember(dash.slug);
    if (slugFromUrl() !== dash.slug) history.replaceState(null, "", `/dashboard-editor?d=${encodeURIComponent(dash.slug)}`);
    document.title = `Stagewatch: edit cards for ${dash.title || dash.slug}`;
    render();
  }

  start();
  SW.heartbeat(2000);   // "Disconnected from Stagewatch" banner if the server stops answering
})();
