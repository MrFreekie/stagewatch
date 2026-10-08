"""Browser-compatibility guard for the web UI.

User dashboards must run on iOS 12 Safari 12.1 (old iPads), Chrome/Edge 80+, Firefox 78+.
The admin page may need iOS 13+. Anything older gets a plain "browser too old" message from
compat.js instead of a blank page. These tests are a cheap, regex-level guard against newer
syntax/APIs creeping into the dashboard-side files; they cannot replace a real device check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "stagewatch" / "web" / "static"
HTML_PAGES = ["index.html", "dashboard.html", "admin.html", "schedule.html"]
# Dashboard-side files (admin.js is allowed to need iOS 13+ / Chrome 80+).
DASHBOARD_JS = ["common.js", "chart.js", "wallclock.js", "dashboard.js", "vendor/qrcode.js"]

FORBIDDEN = {
    "optional chaining ?.": r"\?\.(?!\d)",
    "nullish ??": r"\?\?",
    "logical assignment ??= ||= &&=": r"(\?\?|\|\||&&)=",
    "private #field": r"(?<![\w$])#[A-Za-z_]",
    "static block": r"\bstatic\s*\{",
    "replaceAll": r"\.replaceAll\(",
    ".at()": r"\.at\(",
    "structuredClone": r"\bstructuredClone\b",
    "Object.hasOwn": r"\bObject\.hasOwn\b",
    "findLast": r"\.findLast(Index)?\(",
    "top-level await / await import": r"\bawait\s+import\b",
    "regex lookbehind": r"\(\?<[=!]",
    "regex named group": r"\(\?<[A-Za-z_]",
    "globalThis": r"\bglobalThis\b",
    "Promise.allSettled/any": r"\bPromise\.(allSettled|any)\b",
    "numeric separator": r"\b\d+_\d+\b",
    "BigInt": r"\bBigInt\b|\b\d+n\b",
    "queueMicrotask": r"\bqueueMicrotask\b",
    "AbortSignal.timeout": r"\bAbortSignal\.(timeout|any)\b",
    "hourCycle": r"\bhourCycle\b",
    "optional catch binding": r"\bcatch\s*\{",
    "toSorted/toReversed/with": r"\.(toSorted|toReversed|toSpliced)\(",
    "Array.fromAsync / group": r"\b(Array\.fromAsync|Object\.groupBy|Map\.groupBy)\b",
    "matchMedia addEventListener only": r"matchMedia\([^)]*\)\.addEventListener",
}

# ResizeObserver is Safari 13.1+: allowed only when guarded by a typeof check.
CSS_FORBIDDEN = {
    "aspect-ratio": r"\baspect-ratio\s*:",
    "inset shorthand": r"(?<![\w-])inset\s*:",
    ":is()/:where()": r":(is|where)\(",
    "clamp()": r"\bclamp\(",
    "backdrop-filter": r"\bbackdrop-filter\b",
    "container queries": r"@container\b",
    ":has()": r":has\(",
    "nesting-free check: @layer": r"@layer\b",
}


def _strip_js(src: str) -> str:
    """Remove comments and string/template contents (keeps ${} out too; good enough for a regex scan)."""
    out: list[str] = []
    i, n = 0, len(src)
    prev_sig = ""  # last significant char, to tell a regex literal from division
    while i < n:
        c = src[i]
        two = src[i : i + 2]
        if two == "//":
            while i < n and src[i] != "\n":
                i += 1
        elif two == "/*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "\"'`":
            q = c
            i += 1
            while i < n and src[i] != q:
                i += 2 if src[i] == "\\" else 1
            i += 1
            out.append('""')
            prev_sig = '"'
        elif c == "/" and prev_sig in "(,=:[!&|?{};" + "":
            # regex literal: keep it (lookbehind/named groups live here) but drop the flags
            j = i + 1
            in_class = False
            while j < n and (src[j] != "/" or in_class) and src[j] != "\n":
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                j += 1
            out.append(src[i : j + 1])
            i = j + 1
            prev_sig = "/"
        else:
            out.append(c)
            if not c.isspace():
                prev_sig = c
            i += 1
    return "".join(out)


def _strip_css(src: str) -> str:
    return re.sub(r"/\*.*?\*/", "", src, flags=re.S)


@pytest.mark.parametrize("name", DASHBOARD_JS)
def test_dashboard_js_avoids_newer_syntax_and_apis(name: str) -> None:
    code = _strip_js((STATIC / name).read_text(encoding="utf-8"))
    hits = [
        f"{label}: {m.group(0)!r}"
        for label, pattern in FORBIDDEN.items()
        for m in [re.search(pattern, code)]
        if m
    ]
    assert not hits, f"{name} uses features newer than iOS 12 Safari: {hits}"


def test_resize_observer_is_guarded() -> None:
    for name in DASHBOARD_JS:
        code = _strip_js((STATIC / name).read_text(encoding="utf-8"))
        if "ResizeObserver" in code:
            assert re.search(r"typeof\s+ResizeObserver", code), f"{name}: guard ResizeObserver (Safari 13.1)"


def test_pointer_events_have_touch_fallback() -> None:
    # Pointer events arrive in Safari 13; iOS 12 needs touch/mouse events as well.
    src = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    if "pointerdown" in src:
        assert "touchend" in src and "mousedown" in src


def test_replace_children_is_polyfilled() -> None:
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    assert "Element.prototype.replaceChildren" in common  # Chrome < 86, Safari < 14


def test_dashboard_css_avoids_newer_features() -> None:
    css = _strip_css((STATIC / "style.css").read_text(encoding="utf-8"))
    hits = [label for label, pattern in CSS_FORBIDDEN.items() if re.search(pattern, css)]
    # inset is used by the admin-only restart overlay; tolerate exactly that rule.
    hits = [h for h in hits if h != "inset shorthand" or css.count("inset:") > 1]
    assert not hits, f"style.css uses features newer than iOS 12 Safari: {hits}"


def test_color_mix_has_fallback() -> None:
    css = _strip_css((STATIC / "style.css").read_text(encoding="utf-8"))
    for line in css.splitlines():
        if "color-mix(" in line:
            assert re.search(r"background:\s*rgba?\(", line.split("color-mix(")[0]), (
                f"color-mix needs a preceding plain colour fallback (Safari < 16.2): {line.strip()}"
            )


def test_flex_gap_has_fallback() -> None:
    css = _strip_css((STATIC / "style.css").read_text(encoding="utf-8"))
    assert ".no-flex-gap" in css  # Safari < 14.1 ignores gap on flex containers
    compat = (STATIC / "compat.js").read_text(encoding="utf-8")
    assert "no-flex-gap" in compat


def test_compat_js_exists_and_is_es5() -> None:
    path = STATIC / "compat.js"
    assert path.exists()
    code = _strip_js(path.read_text(encoding="utf-8"))
    # strings are blanked by _strip_js, so only real syntax is checked here
    banned = {
        "arrow function": r"=>",
        "let": r"\blet\b",
        "const": r"\bconst\b",
        "class": r"\bclass\b",
        "template literal": r"`",
        "spread/rest": r"\.\.\.",
        "async/await": r"\b(async|await)\b",
        "nullish/optional chaining": r"\?\?|\?\.",
        "for...of": r"\bfor\s*\([^)]*\bof\b",
    }
    hits = [label for label, pattern in banned.items() if re.search(pattern, code)]
    assert not hits, f"compat.js must stay plain ES5: {hits}"


@pytest.mark.parametrize("page", HTML_PAGES)
def test_every_page_loads_compat_first_and_has_noscript(page: str) -> None:
    html = (STATIC / page).read_text(encoding="utf-8")
    scripts = re.findall(r'<script[^>]*\ssrc="([^"]+)"', html)
    assert scripts[0] == "/static/compat.js", f"{page}: compat.js must be the first script"
    # ...and before any inline script too
    first_inline = re.search(r"<script(?![^>]*\ssrc=)[^>]*>", html)
    if first_inline:
        assert html.index("/static/compat.js") < first_inline.start()
    assert "<noscript>" in html


def test_compat_message_text() -> None:
    compat = (STATIC / "compat.js").read_text(encoding="utf-8")
    assert "too old for Stagewatch" in compat
    assert "iOS 12 or later for dashboards" in compat
    assert "iOS 13" in compat
