"""The web UI never turns text into markup.

Everything on screen is built with DOM calls and textContent (see SW.h and SW.renderMarkdown), so
device names, document text and server messages can never inject HTML or script. This test keeps
the HTML-parsing sinks out of our own scripts. Third-party code under static/vendor/ is exempt.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "stagewatch" / "web" / "static"
SINKS = re.compile(
    r"\b(innerHTML|outerHTML|insertAdjacentHTML|document\s*\.\s*write(ln)?|srcdoc"
    r"|createContextualFragment|DOMParser)\b"
    r"|\beval\s*\(|\bnew\s+Function\b|setAttribute\(\s*[\"'`]on")

# Reviewed exceptions, matched on the exact stripped line. compat.js feature-tests syntax on old
# browsers by parsing fixed strings written in that file (never page or server text).
ALLOWED = {
    ("compat.js", "try { new Function(code); return true; } catch (e) { return !(e instanceof SyntaxError); }"),
}


def _code_only(src: str) -> str:
    """Drop comments so a note saying 'no innerHTML' is not a violation."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(^|\s)//.*$", "", src, flags=re.M)


def test_no_html_parsing_sinks_in_our_scripts():
    files = sorted(STATIC.glob("*.js"))
    assert files, "no scripts found"
    hits = []
    for p in files:
        for i, line in enumerate(_code_only(p.read_text(encoding="utf-8")).splitlines(), 1):
            if SINKS.search(line) and (p.name, line.strip()) not in ALLOWED:
                hits.append(f"{p.name}:{i}: {line.strip()[:80]}")
    assert not hits, "build elements with SW.h / textContent instead:\n" + "\n".join(hits)


def test_setlists_render_without_links():
    """Schedule text is untrusted and public: setlists go through SW.renderMarkdown with links
    left off (the default), on the dashboard and in the admin preview."""
    for name in ("dashboard.js", "schedule-editor.js"):
        code = _code_only((STATIC / name).read_text(encoding="utf-8"))
        assert "SW.renderMarkdown(" in code, name
        assert "links: true" not in code and "links:true" not in code, name


def test_the_check_itself_catches_sinks():
    assert SINKS.search("el.innerHTML = x")
    assert SINKS.search("el.insertAdjacentHTML('beforeend', x)")
    assert SINKS.search("document.write(x)")
    assert not SINKS.search(_code_only("// no innerHTML here\nconst a = 1;"))


def test_admin_help_links_point_at_real_files():
    """The Help card's guide links come from one list; every path must exist in the repo."""
    root = Path(__file__).resolve().parents[1]
    src = (STATIC / "admin.js").read_text(encoding="utf-8")
    block = src.split("const HELP_LINKS = [", 1)[1].split("\n  ];", 1)[0]
    paths = re.findall(r'path: "([^"]+)"', block)
    assert len(paths) >= 10
    assert len(set(paths)) == len(paths), "a guide is linked twice"
    missing = [p for p in paths if not (root / p).is_file()]
    assert not missing, f"Help card links to files that do not exist: {missing}"
    assert 'const HELP_REPO = "https://github.com/MrFreekie/stagewatch/blob/main/";' in src
    assert 'target: "_blank", rel: "noopener noreferrer"' in src
