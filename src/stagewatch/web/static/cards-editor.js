// The card editor for one dashboard: which cards it shows, in what order, each card's own options,
// and the stage it follows. Used by the Edit cards page (dashboard-editor.js). Built with SW.h only.
// SW.cardsEditor(dashboard, onChange, ctx) -> {el, read(), setDefaults(layout), count()}
// ctx = { cards: admin state "cards" (known, defaults, half_capable), contactsPinSet: bool }
(function () {
  "use strict";
  const h = SW.h;
  const field = (label, input) => h("label", { class: "field" }, label, input);
  const val = (el) => el.value.trim();
  const CARD_INFO = {
    env_tiles: ["Site readings", "Tiles for temperature, humidity, pressure, speed of sound and dew point; you can also add today's lows and highs."],
    schedule: ["Schedule", "What is on now and next, and the running order. Stays hidden until the show has a schedule."],
    chart: ["History chart", "Readings over time, with markers."],
    markers: ["Markers", "The marker list, the Add marker box, and how far things have drifted since a marker."],
    sensors: ["Sensor nodes", "Each sensor node, whether it is working, and its latest readings."],
    wall_clock: ["Wall Clock", "The time of day, as plain digits, an LED ring or 7-segment digits. Choose the source in the Wall Clock settings."],
    ontime_timer: ["Ontime Timer", "The countdown Ontime is running, with the event title. Read from Ontime; set its address in the Ontime card."],
    ontime_rundown: ["Ontime Rundown", "The running event, whether Ontime is ahead or behind, the planned and expected end and the list of events with their times. Read from Ontime; set its address in the Ontime card. Event titles and notes are shown on the dashboard, and follow the Ontime card setting \"Show the event title on dashboards\"."],
    equipment: ["Equipment", "Readings from Equipment sensors (amp racks, power supplies), by node. Never part of the site average. Stays hidden until a sensor has the Equipment role."],
    condensation: ["Condensation risk", "Site dew point and how far the air is from it, in plain words (Condensing, Near saturation, Moderate, Low risk). Warns that kit colder than the dew point will fog up, with an optional kit temperature box. A guide, not a guarantee. Needs temperature and humidity sensors."],
    barometer: ["Barometer", "Sea-level pressure dial, 3-hour trend and a rough outlook. A guide only, not a forecast. Needs a pressure sensor (a BME280 node)."],
    spl_limits: ["Sound level limits", "Every limit in force now with its weighting (dB(A), dB(C)), whether it is over, and the next changes. Public wording only. Stays hidden on phones and walls until limits are entered (Sound level limits)."],
    spl_live: ["Sound level", "Up to three sound level values from Smaart, exactly as Smaart reports them, with their timeline. Set it up in the Sound level settings. Stays hidden until values are set up."],
    globcon_meters: ["DirectOut GLOBCON", "Live level meters for a group of channels (for example 1 to 8 or 9 to 16) of one DirectOut GLOBCON controller, with the controller's name and current layer, as GLOBCON reports them. Read-only. Choose Meters (bars) or Lights (one big square per channel that lights above a threshold). Pick the controller and channels here; set the GLOBCON address in the GLOBCON settings."],
    rme_units: ["RME interfaces", "One tile per RME unit (for example a 12Mic): whether it is OK, its core temperature, both power supplies, the clock it follows, the loaded preset and its AVB streams. Reads the unit and never changes it. Not a protection system. Add units in Admin (Integrations). Stays hidden until a unit is set up. Never added by default."],
    power_tree: ["Power tree", "Supplies, intake and distribution units and equipment as a tree: live power, a load bar against each rating, and the unmonitored load at each unit (calculated). Several supplies can stand side by side. Set it up in Power distribution (Admin). Never added by default."],
    power: ["Power", "Watts, volts, amps, frequency and energy from Shelly power meters, as the meters report them. Read only. Never part of a site average. Add meters in the Shelly power meters settings. Stays hidden until a meter has been added."],
    documents: ["Plans on file", "The event's safety and risk documents you have shared with this dashboard (Admin → Documents). The wall shows titles only."],
    contacts: ["Onsite contacts", "Key people for this event: job title, name, radio channel and area (Admin → Onsite contacts). Phone numbers only when you allow them in three places: Onsite contacts, this dashboard, and the contact."],
    witmotion_tilt: ["Inclinometer (WitMotion)", "Roll and/or pitch from each WitMotion tilt sensor, in degrees, with a battery gauge and the settings it reports. Advisory only: a guide, not a rigging safety system. Set the sensors up in Admin (Integrations, WitMotion tilt sensors). Stays hidden until one is set up."],
    pi_host: ["Raspberry Pi", "The health of the Raspberry Pi Stagewatch runs on: temperature, power supply (under-voltage), speed, load, memory, disk and Wi-Fi signal. Hidden unless Stagewatch runs on a Pi."],
    alignment_drift: ["Alignment drift", "How far each delay path has drifted since the Aligned marker, as the air warms or cools, with the delay that would match. Advice only: Stagewatch never changes your delays. Set the paths up in Admin (FOH advice). Stays hidden until a path is set up."],
    hf_air_loss: ["HF air loss", "Estimated change in top end lost to the air (4 kHz and above) at FOH and Rear since the Soundcheck marker. Advice only, not a measurement. Set the positions up in Admin (FOH advice). Stays hidden until a position is set up."],
    wifi: ["Wi-Fi", "The crew internet: network name, password and a QR code that joins it on a phone. Only share guest or crew internet here, never the network your show equipment runs on. Set the networks up in Admin (Wi-Fi). Stays hidden until a network is added. Never added by default."],
    about: ["About Stagewatch", "A small advert: the name, one line and a QR code that opens the Stagewatch project page on a phone. Shows no show data."],
    connect_footer: ["Open on a tablet", "This dashboard's address and a QR code, below all the other cards."],
  };

  // The card editor for one dashboard (the Edit cards page): which cards it shows, in what order, and its stage.
  // Returns {el, read(), setDefaults(layout), count()}.
  function cardsEditor(d, onChange, ctx) {
    const admin = { cards: ctx.cards, config: { contacts: { reveal_pin_set: !!ctx.contactsPinSet } } };
    const known = (admin.cards && admin.cards.known) || Object.keys(CARD_INFO);
    const halfOk = (admin.cards && admin.cards.half_capable) || [];
    const sizes = Object.assign({}, d.card_sizes || {});   // card id -> "full" | "half"
    let items = [];
    const ul = h("ul", { class: "card-picker" });
    const fill = (list) => {
      const on = list.filter((id, i) => SW.validCardId(id) && known.indexOf(SW.cardBase(id)) >= 0 && list.indexOf(id) === i);
      items = on.map((id) => ({ id, on: true })).concat(known.filter((id) => on.indexOf(id) < 0).map((id) => ({ id, on: false })));
      draw();
    };
    const move = (i, dir) => {
      const j = i + dir;
      if (j < 0 || j >= items.length) return;
      const t = items[i]; items[i] = items[j]; items[j] = t;
      draw();
      // keep the keyboard on the card that moved
      const btn = ul.children[j] && ul.children[j].querySelector(dir < 0 ? ".card-up" : ".card-down");
      if (btn && !btn.disabled) btn.focus();
    };
    // At the card limit the unticked boxes are greyed out (untick one to free a place).
    const MAX = 16;   // core/cards.py MAX_CARDS
    const boxes = [];
    const limitBoxes = () => {
      const full = items.filter((it) => it.on).length >= MAX;
      for (const b of boxes) b.el.disabled = full && !b.it.on;
      addBtn.disabled = full || items.filter((it) => SW.cardBase(it.id) === "globcon_meters").length >= 16;
    };
    // Further copies of the GLOBCON card ("globcon_meters#2" ...): added and removed here, never ticked off.
    const removeItem = (i) => {
      const id = items[i].id;
      items.splice(i, 1);
      delete gcEditors[id]; delete sizes[id]; delete moreSaved[id];
      draw();
    };
    const addGlobcon = () => {
      if (items.filter((it) => it.on).length >= MAX) return;
      let n = 2;
      while (items.some((it) => it.id === `globcon_meters#${n}`) && n < 16) n++;
      const id = `globcon_meters#${n}`;
      if (items.some((it) => it.id === id)) return;
      let at = -1;
      items.forEach((it, i) => { if (SW.cardBase(it.id) === "globcon_meters") at = i; });
      items.splice(at + 1, 0, { id, on: true });
      draw();
      const li = ul.children[at + 1];
      if (li) { const f = li.querySelector("select"); if (f) f.focus(); }
    };
    const addBtn = h("button", { type: "button", class: "btn", onclick: addGlobcon }, "Add another GLOBCON card");
    const draw = () => {
      boxes.length = 0;
      ul.replaceChildren(...items.map((it, i) => {
        const base = SW.cardBase(it.id), inst = it.id !== base;
        const info0 = CARD_INFO[base] || [base, ""];
        const info = inst ? [`${info0[0]} ${it.id.split("#")[1]}`, "Another card with its own controller, channels and display."] : info0;
        const box = h("input", { type: "checkbox", checked: it.on, hidden: inst, onchange: () => { it.on = box.checked; if (lookField) lookField.style.display = box.checked ? "" : "none"; limitBoxes(); onChange(); } });
        boxes.push({ el: box, it });
        const lookField = it.id === "wall_clock" ? h("label", { class: "field card-look", style: it.on ? "" : "display:none" }, h("span", {}, "Wall Clock look"), clockStyle)
          : it.id === "env_tiles" ? h("label", { class: "field card-look", style: it.on ? "" : "display:none" }, h("span", {}, "Choose readings"), tileList) : null;
        const sizeSel = halfOk.indexOf(base) < 0 ? null : h("select", { class: "touch", "aria-label": `${info[0]} size`, onchange: () => { sizes[it.id] = sizeSel.value; onChange(); } }, h("option", { value: "full" }, "Full"), h("option", { value: "half" }, "Half"));
        if (sizeSel) sizeSel.value = sizes[it.id] === "half" ? "half" : "full";
        const gcField = base !== "globcon_meters" ? null : editorFor(it.id).el;
        if (gcField) gcField.style.display = it.on ? "" : "none";
        if (gcField) box.addEventListener("change", () => { gcField.style.display = box.checked ? "" : "none"; });
        const ctField = it.id !== "contacts" ? null : h("div", { class: "card-look", style: it.on ? "" : "display:none" },
          h("label", { class: "field" }, h("span", {}, "Only this area (empty = all)"), ctArea),
          h("label", { class: "check" }, ctPhones, " Show phone numbers"), ctWarn);
        if (ctField) box.addEventListener("change", () => { ctField.style.display = box.checked ? "" : "none"; });
        const sizeField = sizeSel ? h("label", { class: "field card-look", style: it.on ? "" : "display:none" }, h("span", { title: "Phones always use the full width" }, "Card width"), sizeSel) : null;
        if (sizeField) box.addEventListener("change", () => { sizeField.style.display = box.checked ? "" : "none"; });
        return h("li", {},
          h("label", { class: "card-pick" }, box, h("span", {}, h("strong", {}, info[0]), info[1] ? h("span", { class: "muted" }, info[1]) : null)),
          lookField,
          gcField,
          ctField,
          sizeField,
          inst ? h("button", { type: "button", class: "card-remove", "aria-label": `Remove ${info[0]}`, title: "Remove this card", onclick: () => removeItem(i) }, "Remove") : null,
          h("button", { type: "button", class: "card-up", "aria-label": `Move ${info[0]} up`, title: "Move up", disabled: i === 0, onclick: () => move(i, -1) }, "▲"),
          h("button", { type: "button", class: "card-down", "aria-label": `Move ${info[0]} down`, title: "Move down", disabled: i === items.length - 1, onclick: () => move(i, 1) }, "▼"));
      }));
      limitBoxes();
      onChange();
    };
    // One GLOBCON levels card's options: which controller (1 to 16), which channels, Meters or Lights.
    // Each GLOBCON card on the dashboard (the first and any "#2" ...) gets its own set. `saved` is that card's
    // saved options (undefined for a card just added); `sfx` makes the field labels unique (" 2" ...).
    function gcEditor(saved, sfx) {
    const gcSaved = saved || { controller: 1, strips: 8, range: "1-8" };
    const gcCtrl = h("select", { class: "touch", "aria-label": `GLOBCON controller${sfx}` }, Array.from({ length: 16 }, (_, i) => h("option", { value: String(i + 1) }, `Controller ${i + 1}`)));
    gcCtrl.value = String(gcSaved.controller >= 1 && gcSaved.controller <= 16 ? gcSaved.controller : 1);
    // Channels are the strip numbers on the controller's current layer. A dashboard saved before the groups existed
    // (no range) keeps its "first 4 / 8 with a level" choice as two extra options, shown only while it is in use.
    const gcGroups = [["1-4", "Channels 1 to 4"], ["5-8", "Channels 5 to 8"], ["1-8", "Channels 1 to 8"], ["9-12", "Channels 9 to 12"], ["13-16", "Channels 13 to 16"], ["9-16", "Channels 9 to 16"], ["1-16", "Channels 1 to 16"]];
    const gcOld = !gcSaved.range;
    const gcStrips = h("select", { class: "touch", "aria-label": `GLOBCON channels shown${sfx}` },
      gcOld ? h("option", { value: "old-4" }, "First 4 channels with a level (older setting)") : null,
      gcOld ? h("option", { value: "old-8" }, "First 8 channels with a level (older setting)") : null,
      ...gcGroups.map(([v, t]) => h("option", { value: v }, t)));
    gcStrips.value = gcOld ? (gcSaved.strips === 4 ? "old-4" : "old-8") : gcSaved.range;
    // Display: Meters (bars) or Lights (one big square per ticked channel). In Lights mode each channel has a table row;
    // a new row starts with sensible defaults (-40 dB, green, microphone, hold 1 s) so most people only tick channels.
    // Thresholds are in dB exactly as GLOBCON reports them.
    const gcMode = h("select", { class: "touch", "aria-label": `GLOBCON display${sfx}` }, h("option", { value: "meters" }, "Meters (bars)"), h("option", { value: "lights" }, "Lights (one square per channel)"));
    gcMode.value = gcSaved.mode === "lights" ? "lights" : "meters";
    const GC_COLOURS = [["green", "Green"], ["amber", "Amber"], ["red", "Red"], ["blue", "Blue"], ["cyan", "Cyan"], ["magenta", "Magenta"], ["white", "White"], ["orange", "Orange"]];
    const GC_ICONS = [["microphone", "Microphone"], ["speaker", "Speaker"], ["timecode", "Timecode clock"], ["play", "Play"], ["megaphone", "Megaphone"], ["camera", "Camera"], ["headset", "Headset"], ["signal", "Signal"]];
    const GC_HOLDS = [0, 0.5, 1, 2, 3, 5, 10];
    const GC_DEF = { threshold_db: -40, colour: "green", icon: "microphone", hold_s: 1 };
    const gcLights = {};     // channel number -> {threshold_db, colour, icon, hold_s} for the ticked channels
    for (const e of (Array.isArray(gcSaved.lights) ? gcSaved.lights : [])) if (e && Number.isInteger(e.strip)) gcLights[e.strip] = { threshold_db: e.threshold_db, colour: e.colour, icon: e.icon, hold_s: e.hold_s };
    const gcRangeNow = () => {
      const v = gcStrips.value;
      if (v.indexOf("old-") === 0) return [1, 16];
      const p = v.split("-");
      return [Number(p[0]), Number(p[1])];
    };
    const gcLightsBox = h("div", { class: "gc-lights-edit" });
    const gcLightsDraw = () => {
      const r = gcRangeNow(), rows = [];
      gcLightsBox.style.display = gcMode.value === "lights" ? "" : "none";
      for (let n = r[0]; n <= r[1]; n++) {
        const cur = gcLights[n], on = !!cur, v = cur || GC_DEF;
        const box = h("input", { type: "checkbox", checked: on, "aria-label": `Light for channel ${n}${sfx}` });
        const thr = h("input", { class: "touch", type: "number", min: "-72", max: "0", step: "1", value: String(isFinite(v.threshold_db) ? v.threshold_db : GC_DEF.threshold_db), "aria-label": `Channel ${n} threshold in dB${sfx}`, disabled: !on });
        const col = h("select", { class: "touch", "aria-label": `Channel ${n} colour${sfx}`, disabled: !on }, GC_COLOURS.map(([k, t]) => h("option", { value: k }, t)));
        col.value = GC_COLOURS.some((c) => c[0] === v.colour) ? v.colour : GC_DEF.colour;
        const ico = h("select", { class: "touch", "aria-label": `Channel ${n} icon${sfx}`, disabled: !on }, GC_ICONS.map(([k, t]) => h("option", { value: k }, t)));
        ico.value = GC_ICONS.some((c) => c[0] === v.icon) ? v.icon : GC_DEF.icon;
        const holds = GC_HOLDS.indexOf(v.hold_s) >= 0 || !isFinite(v.hold_s) ? GC_HOLDS : GC_HOLDS.concat([v.hold_s]).sort((a, b) => a - b);
        const hold = h("select", { class: "touch", "aria-label": `Channel ${n} hold${sfx}`, disabled: !on }, holds.map((s) => h("option", { value: String(s) }, s === 0 ? "Instant" : `${s} s`)));
        hold.value = String(isFinite(v.hold_s) ? v.hold_s : GC_DEF.hold_s);
        const sync = () => {
          if (box.checked) gcLights[n] = { threshold_db: Math.max(-72, Math.min(0, Number(thr.value) || 0)), colour: col.value, icon: ico.value, hold_s: Number(hold.value) };
          else delete gcLights[n];
          for (const c of [thr, col, ico, hold]) c.disabled = !box.checked;
          onChange();
        };
        for (const c of [box, thr, col, ico, hold]) c.addEventListener("change", sync);
        rows.push(h("tr", {}, h("td", {}, h("label", { class: "check" }, box, ` Channel ${n}`)), h("td", {}, thr), h("td", {}, col), h("td", {}, ico), h("td", {}, hold)));
      }
      gcLightsBox.replaceChildren(
        h("p", { class: "muted hint" }, "Tick the channels to show as lights. A light is on when its channel is at or above the threshold, in dB exactly as GLOBCON reports it, and stays on for the hold time after it drops. Not yet checked against every GLOBCON channel type: please try a threshold against GLOBCON's own meters."),
        h("div", { class: "table-wrap" }, h("table", { class: "gc-lights-table" },
          h("thead", {}, h("tr", {}, h("th", {}, "Channel"), h("th", {}, "Threshold (dB)"), h("th", {}, "Colour"), h("th", {}, "Icon"), h("th", {}, "Hold"))),
          h("tbody", {}, rows))));
    };
    const gcRead = () => {
      const base = gcStrips.value.indexOf("old-") === 0 ? { controller: Number(gcCtrl.value), strips: Number(gcStrips.value.slice(4)), range: null } : { controller: Number(gcCtrl.value), strips: 8, range: gcStrips.value };
      const r = gcRangeNow();
      const lights = Object.keys(gcLights).map(Number).filter((n) => n >= r[0] && n <= r[1]).sort((a, b) => a - b).map((n) => Object.assign({ strip: n }, gcLights[n]));
      return Object.assign(base, { mode: gcMode.value, lights });
    };
    gcCtrl.addEventListener("change", onChange);
    gcStrips.addEventListener("change", () => { gcLightsDraw(); onChange(); });
    gcMode.addEventListener("change", () => { gcLightsDraw(); onChange(); });
    gcLightsDraw();
    const el = h("div", { class: "card-look" },
      h("label", { class: "field" }, h("span", {}, "GLOBCON controller"), gcCtrl),
      h("label", { class: "field" }, h("span", {}, "Channels shown"), gcStrips),
      h("label", { class: "field" }, h("span", {}, "Display"), gcMode),
      gcLightsBox);
    return { el, read: gcRead };
    }
    const gcEditors = {};   // card id -> gcEditor
    const moreSaved = Object.assign({}, d.globcon_more);
    const editorFor = (id) => gcEditors[id] || (gcEditors[id] = gcEditor(id === "globcon_meters" ? (d.globcon || {}) : moreSaved[id], id.indexOf("#") > 0 ? ` ${id.split("#")[1]}` : ""));
    // Onsite contacts card: phone numbers on this screen (also needs the switch in Onsite contacts and the contact's own tick).
    const ctArea = h("input", { class: "touch", value: d.contacts_area || "", maxlength: 40, list: "stage-list", placeholder: "e.g. Main stage", autocomplete: "off", oninput: () => onChange() });
    const ctWarn = h("p", { class: "warn-text hint" });
    const ctWarnFill = () => ctWarn.replaceChildren(ctPhones.checked ? `▲ Dashboards have no login: anyone on the show network who opens /d/${d.slug || "…"} can see these numbers${admin.config.contacts && admin.config.contacts.reveal_pin_set ? " after typing the contacts PIN" : ""}.` : "");
    const ctPhones = h("input", { type: "checkbox", checked: !!d.show_contact_phones, onchange: () => { ctWarnFill(); onChange(); } });
    ctWarnFill();
    const stage = h("input", { class: "touch", value: d.stage || "", maxlength: 40, list: "stage-list", placeholder: "e.g. Main stage", autocomplete: "off" });
    const clockStyle = h("select", { class: "touch" }, h("option", { value: "digits" }, "Plain digits"), h("option", { value: "ring" }, "LED ring"), h("option", { value: "segments" }, "7-segment digits"));
    clockStyle.value = ["ring", "segments"].indexOf(d.clock_style) >= 0 ? d.clock_style : "digits";
    // Site readings card: which tiles show, in order. No choice saved = the default five (tiles: []).
    const TILE_INFO = { temperature: "Temperature", humidity: "Humidity", pressure: "Pressure", speed_of_sound: "Speed of sound", dew_point: "Dew point", highs_lows: "Today's lows and highs" };
    const tileAll = (admin.cards && admin.cards.site_tiles) || Object.keys(TILE_INFO);
    const tileDef = (admin.cards && admin.cards.default_tiles) || tileAll.filter((t) => t !== "highs_lows");
    const tileSaved = (d.tiles || []).filter((t) => tileAll.indexOf(t) >= 0);
    const tileStart = tileSaved.length ? tileSaved : tileDef.filter((t) => t !== "dew_point" || d.show_dew_point !== false);
    let tiles = tileStart.map((id) => ({ id, on: true })).concat(tileAll.filter((t) => tileStart.indexOf(t) < 0).map((id) => ({ id, on: false })));
    const tileList = h("ul", { class: "card-picker tile-picker" });
    const tileDraw = () => tileList.replaceChildren(...tiles.map((t, i) => {
      const mv = (dir) => { const j = i + dir; if (j < 0 || j >= tiles.length) return; const x = tiles[i]; tiles[i] = tiles[j]; tiles[j] = x; tileDraw(); onChange(); };
      return h("li", {},
        h("label", { class: "card-pick" }, h("input", { type: "checkbox", checked: t.on, onchange: (ev) => { t.on = ev.target.checked; onChange(); } }), h("span", {}, TILE_INFO[t.id] || t.id)),
        h("button", { type: "button", class: "card-up", "aria-label": `Move ${TILE_INFO[t.id] || t.id} up`, title: "Move up", disabled: i === 0, onclick: () => mv(-1) }, "▲"),
        h("button", { type: "button", class: "card-down", "aria-label": `Move ${TILE_INFO[t.id] || t.id} down`, title: "Move down", disabled: i === tiles.length - 1, onclick: () => mv(1) }, "▼"));
    }));
    tileDraw();
    const tilesRead = () => {
      const on = tiles.filter((t) => t.on).map((t) => t.id);
      return on.join() === tileDef.join() ? [] : on;   // the default set in the default order is stored as "no choice"
    };
    const dewOn = () => { const t = tiles.find((x) => x.id === "dew_point"); return !t || t.on; };
    // Ids from a newer Stagewatch (kept in the settings after a downgrade) can't be saved by this one.
    const newer = (d.cards || []).filter((id) => SW.validCardId(id) && known.indexOf(SW.cardBase(id)) < 0);
    fill(d.cards || (admin.cards && admin.cards.defaults.tablet) || []);
    const el = h("div", { class: "cards-panel" },
      h("p", { class: "muted hint" }, "Tick the cards this dashboard shows. Use ▲ and ▼ to change the order, top to bottom. With no cards ticked, the screen shows only alarms. Wall Clock is never switched on by default: tick it here if you want it."),
      newer.length ? h("p", { class: "warn-text hint" }, `This dashboard also lists cards from a newer version of Stagewatch (${newer.join(", ")}). This version can't show them, and saving here removes them.`) : null,
      d.layout === "wall" ? h("p", { class: "hint de-wallhint" }, "A wall screen does not scroll: pick the few cards that matter. Anything below the bottom of the screen cannot be seen.") : null,
      ul,
      h("div", { class: "row", style: "margin-top:10px" }, addBtn),
      h("div", { class: "row", style: "margin-top:10px" },
        field("Stage", stage)),
      h("p", { class: "muted hint" }, "Stage: which stage this screen follows, for cards that show one stage (like the schedule). Leave it empty to show every stage. The Wall Clock look (beside the Wall Clock card) is how that card is drawn on this screen. The ring and 7-segment looks are always red on black. Half-size cards sit side by side with another half-size card on tablets and wall screens. On a phone every card is full width."));
    return {
      el,
      read: () => ({ cards: items.filter((it) => it.on).map((it) => it.id), stage: val(stage), clock_style: clockStyle.value, show_dew_point: dewOn(), tiles: tilesRead(), show_contact_phones: ctPhones.checked, contacts_area: val(ctArea), card_sizes: Object.fromEntries(Object.entries(sizes).filter(([id, v]) => v === "half" && halfOk.indexOf(SW.cardBase(id)) >= 0)), globcon: editorFor("globcon_meters").read(), globcon_more: Object.fromEntries(items.filter((it) => it.on && it.id.indexOf("#") > 0).map((it) => [it.id, editorFor(it.id).read()])) }),
      setDefaults: (layout) => fill((admin.cards && admin.cards.defaults[layout]) || []),
      count: () => items.filter((it) => it.on).length,
      max: MAX,
    };
  }

  SW.CARD_INFO = CARD_INFO;
  SW.cardsEditor = cardsEditor;
})();
