// Browser check, loaded first on every page. Strict ES5 on purpose: it has to run
// (and explain itself) on browsers too old for the rest of Stagewatch, where the page
// would otherwise just stay blank. No ES2015+ syntax in this file (tests enforce it).
//
// Dashboards (/ and /d/<name>): iOS 12 Safari, Chrome/Edge 80+, Firefox 78+.
// Admin page (/admin): iOS 13+, Chrome/Edge 80+, Firefox 78+.
(function () {
  "use strict";

  // True if `code` parses. Only a SyntaxError counts as "not supported"; anything else
  // (for example a security policy blocking Function) is treated as fine.
  function parses(code) {
    try { new Function(code); return true; } catch (e) { return !(e instanceof SyntaxError); }
  }

  var isAdmin = /^\/admin(\/|$)/.test(location.pathname);
  var proto = (typeof Element !== "undefined" && Element.prototype) || {};
  var ok = !!(
    window.Promise && window.fetch && window.WebSocket && window.AbortController &&
    Object.entries && Object.fromEntries && Array.prototype.flat && proto.append && proto.prepend &&
    window.CSS && CSS.supports && CSS.supports("color", "var(--x)") &&
    parses("class A { get x() { return 1; } } var f = async (a, ...b) => { await a; }; var o = { ...{}, a: `t` };")
  );
  // Admin page: also needs ?? and ?. (iOS 13, Chrome 80, Firefox 78).
  if (ok && isAdmin) ok = parses("var a = null; var b = (a ?? 1) + (a?.x ?? 2);");

  window.SW_BROWSER_OK = ok;

  var MESSAGE = "This browser is too old for Stagewatch. Use a recent Chrome, Edge, Firefox or Safari " +
    "(iPad: iOS 12 or later for dashboards, iOS 13 or later for the admin page).";
  var ADMIN_MESSAGE = "This browser is too old for the Stagewatch admin page. Use a recent Chrome, Edge, " +
    "Firefox or Safari (iPad: iOS 13 or later). Dashboards still work on iPads with iOS 12 or later.";

  function showMessage() {
    var body = document.body;
    while (body.firstChild) body.removeChild(body.firstChild);
    var p = document.createElement("p");
    p.setAttribute("role", "alert");
    p.style.cssText = "font: 20px/1.4 sans-serif; max-width: 36em; margin: 40px auto; padding: 0 16px;";
    p.textContent = isAdmin ? ADMIN_MESSAGE : MESSAGE;
    body.appendChild(p);
    document.documentElement.style.background = "#fff";
    body.style.background = "#fff";
    body.style.color = "#000";
  }

  // Old Safari (before 14.1) ignores `gap` on flex containers. Detect it and let the
  // stylesheet fall back to margins (html.no-flex-gap).
  function flexGapMissing() {
    var box = document.createElement("div");
    box.style.cssText = "display:flex;flex-direction:column;row-gap:1px;position:absolute;visibility:hidden";
    for (var i = 0; i < 2; i++) {
      var c = document.createElement("div");
      c.style.cssText = "height:1px";
      box.appendChild(c);
    }
    document.body.appendChild(box);
    var missing = box.scrollHeight !== 3;
    document.body.removeChild(box);
    return missing;
  }

  function ready() {
    if (!ok) { showMessage(); return; }
    if (flexGapMissing()) document.documentElement.className += " no-flex-gap";
  }
  if (document.body) ready();
  else document.addEventListener("DOMContentLoaded", ready);
})();
