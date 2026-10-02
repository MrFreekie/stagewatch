// Node test for SW.renderMarkdown (common.js), using a tiny fake DOM.
// Run: node tests/js/markdown_test.js   (tests/test_markdown.py runs it when node is installed)
// A real-browser run of the same cases: open tests/js/markdown_test.html from a local server.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const STATIC = path.join(__dirname, "..", "..", "src", "stagewatch", "web", "static");

// ---- minimal fake DOM: only what renderMarkdown uses. Any use of innerHTML-style setters throws.
class FakeNode { constructor() { this.childNodes = []; } }
class FakeText extends FakeNode {
  constructor(s) { super(); this.nodeType = 3; this.data = String(s); }
}
class FakeElement extends FakeNode {
  constructor(tag) { super(); this.nodeType = 1; this.tagName = tag.toUpperCase(); this.className = ""; this.attrs = {}; }
  appendChild(c) { this.childNodes.push(c); return c; }
  append(...cs) { for (const c of cs) this.appendChild(typeof c === "string" ? new FakeText(c) : c); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  set textContent(s) { this.childNodes = s === "" ? [] : [new FakeText(s)]; }
  get textContent() { return this.childNodes.map((c) => c.textContent === undefined ? c.data : c.textContent).join(""); }
  set href(v) { this.attrs.href = String(v); }
  get href() { return this.attrs.href; }
  set rel(v) { this.attrs.rel = String(v); }
  get rel() { return this.attrs.rel; }
  set target(v) { this.attrs.target = String(v); }
  get target() { return this.attrs.target; }
  set innerHTML(_) { throw new Error("innerHTML used"); }
  set outerHTML(_) { throw new Error("outerHTML used"); }
}
FakeElement.prototype.replaceChildren = function () {};
class FakeFragment extends FakeNode {
  constructor() { super(); this.nodeType = 11; }
  appendChild(c) { this.childNodes.push(c); return c; }
}
const document = {
  createElement: (t) => new FakeElement(t),
  createTextNode: (s) => new FakeText(s),
  createDocumentFragment: () => new FakeFragment(),
};
const ctx = { Intl, Date, Number, Math, Object, Array, String, URL, console, window: {}, document, Element: FakeElement, Node: FakeNode };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(STATIC, "common.js"), "utf8"), ctx, { filename: "common.js" });
const SW = vm.runInContext("SW", ctx);

// ---- helpers: serialise to a compact string and collect elements
function ser(n) {
  if (n.nodeType === 3) return "T(" + n.data + ")";
  const attrs = Object.keys(n.attrs || {}).sort().map((k) => `${k}=${n.attrs[k]}`).join(",");
  const kids = n.childNodes.map(ser).join("");
  return n.nodeType === 11 ? kids : `<${n.tagName.toLowerCase()}${attrs ? " " + attrs : ""}>${kids}</>`;
}
function all(n, out) {
  out = out || [];
  for (const c of n.childNodes) { if (c.nodeType === 1) { out.push(c); all(c, out); } }
  return out;
}
const render = (s, o) => SW.renderMarkdown(s, o);
const tags = (f) => all(f).map((e) => e.tagName.toLowerCase());
const plain = (f) => {
  let out = "";
  (function walk(n) { for (const c of n.childNodes) { if (c.nodeType === 3) out += c.data; else walk(c); } })(f);
  return out;
};

let fails = 0, count = 0;
function eq(got, exp, what) {
  count++;
  const g = JSON.stringify(got), e = JSON.stringify(exp);
  if (g !== e) { fails++; console.log(`FAIL ${what}: got ${g}, expected ${e}`); }
}
function ok(cond, what) { count++; if (!cond) { fails++; console.log(`FAIL ${what}`); } }

// ---- normal rendering
eq(ser(render("")), "", "empty");
eq(ser(render(null)), "", "null");
eq(ser(render("Hello world")), "<p>T(Hello world)</>", "plain text paragraph");
eq(tags(render("# One\n## Two\n### Three")), ["h3", "h4", "h5"], "heading levels map to h3-h5");
eq(tags(render("#### Four")), ["p"], "four hashes are a paragraph");
eq(plain(render("#### Four")), "#### Four", "four hashes stay literal");
eq(tags(render("a\nb")), ["p", "br"], "single newline is a line break");
eq(tags(render("a\n\nb")), ["p", "p"], "blank line splits paragraphs");
eq(tags(render("**bold** and *italic*")), ["p", "strong", "em"], "bold and italic");
eq(tags(render("**a *b* c**")), ["p", "strong", "em"], "italic inside bold");
eq(tags(render("`x < y`")), ["p", "code"], "inline code");
eq(plain(render("`<b>`")), "<b>", "code content is text");
eq(tags(render("- a\n- b\n- c")), ["ul", "li", "li", "li"], "bullet list");
eq(tags(render("* a\n+ b")), ["ul", "li", "li"], "star and plus bullets");
eq(tags(render("1. a\n2. b")), ["ol", "li", "li"], "numbered list");
eq(tags(render("- a\n1. b")), ["ul", "li", "ol", "li"], "list type change starts a new list");
eq(tags(render("para\n- a")), ["p", "ul", "li"], "list ends a paragraph");
eq(tags(render("2 * 3 * 4")), ["p"], "spaced asterisks are literal");
eq(plain(render("2 * 3 * 4")), "2 * 3 * 4", "spaced asterisks keep text");
eq(plain(render("a ** b")), "a ** b", "unmatched bold stays literal");
eq(plain(render("**unclosed")), "**unclosed", "unclosed bold literal");
eq(plain(render("a\r\nb")), "ab", "CRLF handled");

// ---- links
const a1 = all(render("[Site](https://example.org/x?a=1)")).filter((e) => e.tagName === "A")[0];
ok(a1 && a1.attrs.href === "https://example.org/x?a=1", "https link becomes <a>");
eq(a1 && a1.attrs.rel, "noopener noreferrer", "link rel");
eq(a1 && a1.attrs.target, "_blank", "link target");
eq(tags(render("[x](http://example.org)")), ["p", "a"], "http link");
eq(tags(render("[mail](mailto:a@example.org)")), ["p", "a"], "mailto link");
eq(tags(render("[**b**](https://example.org)")), ["p", "a", "strong"], "emphasis inside a link label");
eq(tags(render("[[x]](https://example.org)")), ["p", "a"], "nested brackets in a label");
eq(tags(render("[a [b](https://example.org)](https://example.org)")), ["p", "a"], "no links inside links");
eq(tags(render("[x](https://example.org)", { links: false })), ["p"], "links: false shows text");
eq(plain(render("[x](https://example.org)", { links: false })), "[x](https://example.org)", "links: false keeps source");

// ---- safety: nothing here may ever become an element other than the allowed ones
const ALLOWED = ["p", "br", "h3", "h4", "h5", "ul", "ol", "li", "strong", "em", "code", "a"];
const hostile = [
  "<script>alert(1)</script>",
  "<img src=x onerror=alert(1)>",
  "<b onclick=alert(1)>x</b>",
  "<iframe src=//evil></iframe>",
  "[x](javascript:alert(1))",
  "[x](JaVaScRiPt:alert(1))",
  "[x](  javascript:alert(1))",
  "[x](java\nscript:alert(1))",
  "[x](java\tscript:alert(1))",
  "[x](&#106;avascript:alert(1))",
  "[x](data:text/html;base64,PHNjcmlwdD4=)",
  "[x](vbscript:msgbox(1))",
  "[x](file:///etc/passwd)",
  "[x](//evil.example/)",
  "[x](/relative/path)",
  "[x](relative.html)",
  "[x](#frag)",
  "[x](https://user:pass@example.org/)",
  "[[[[[x]]]]](javascript:alert(1))",
  "[a](javascript:alert(1)) and [b](https://ok.example)",
  "![img](https://example.org/a.png)",
  "<https://example.org>",
  "javascript:alert(1)",
];
for (const s of hostile) {
  const f = render(s);
  const bad = tags(f).filter((t) => ALLOWED.indexOf(t) < 0);
  eq(bad, [], "only allowed elements for " + JSON.stringify(s));
  for (const a of all(f).filter((e) => e.tagName === "A")) {
    ok(/^(https?:\/\/[^@]*$|mailto:)/.test(a.attrs.href),
      "link href is http/https/mailto for " + JSON.stringify(s));
  }
}
eq(tags(render("[x](javascript:alert(1))")), ["p"], "javascript: link is not an <a>");
eq(plain(render("[x](javascript:alert(1))")), "[x](javascript:alert(1))", "javascript: link shown as text");
eq(tags(render("[x](data:text/html,hi)")), ["p"], "data: link is not an <a>");
eq(tags(render("[x](/rel)")), ["p"], "relative link is not an <a>");
eq(tags(render("[x](https://user:pass@example.org/)")), ["p"], "credentials in URL rejected");
eq(tags(render("[a](javascript:alert(1)) and [b](https://ok.example)")), ["p", "a"], "only the safe link is kept");
eq(plain(render("<script>alert(1)</script>")), "<script>alert(1)</script>", "script shown as text");
eq(plain(render("<img src=x onerror=alert(1)>")), "<img src=x onerror=alert(1)>", "img shown as text");
eq(tags(render("<script>alert(1)</script>")), ["p"], "script is not an element");
eq(plain(render("# <b>x</b>")), "<b>x</b>", "HTML in a heading is text");
eq(plain(render("- <img onerror=1>")), "<img onerror=1>", "HTML in a list item is text");
ok(SW.mdSafeUrl("https://example.org") === "https://example.org/", "mdSafeUrl normalises");
ok(SW.mdSafeUrl("javascript:alert(1)") === null, "mdSafeUrl rejects javascript:");
ok(SW.mdSafeUrl("") === null, "mdSafeUrl rejects empty");

// ---- limits: must finish quickly and stay bounded
function timed(what, s, maxMs) {
  const t0 = Date.now();
  const f = render(s);
  const ms = Date.now() - t0;
  ok(ms < maxMs, `${what} took ${ms} ms (limit ${maxMs})`);
  return f;
}
let f = timed("huge input", "x".repeat(5000000), 1000);
ok(plain(f).length < 110000, "huge input is truncated");
ok(plain(f).indexOf("shortened") >= 0, "truncation is announced");
f = timed("many lines", "- a\n".repeat(50000), 1000);
ok(tags(f).length <= 2005, "line count is capped");
f = timed("many emphasis marks", "*a* ".repeat(25000), 1000);
ok(all(f).length <= 7000, "element count is capped: " + all(f).length);
f = timed("many unmatched stars", "*a ".repeat(600), 1000);
f = timed("many unmatched brackets", "[".repeat(1900), 1000);
f = timed("many unmatched brackets, long", ("[a](").repeat(500), 1000);
f = timed("deep emphasis", "**a *b **c *d **e *f* e** d* c** b* a**", 1000);
ok(all(f).length < 20, "emphasis nesting is bounded");
f = timed("one very long line", "*a* ".repeat(5000), 1000);
eq(tags(f), ["p"], "over-long line is shown as plain text");
f = timed("nested brackets", "[".repeat(1000) + "x" + "]".repeat(1000) + "(https://example.org)", 1000);
f = timed("backticks", "`".repeat(100000), 1000);

console.log(`${count} checks, ${fails} failed`);
process.exit(fails ? 1 : 0);
