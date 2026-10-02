"""The web UI never turns text into markup.

Everything on screen is built with DOM calls and textContent (see SW.h and SW.renderMarkdown), so
device names, document text and server messages can never inject HTML or script. This test keeps
the HTML-parsing sinks out of our own scripts. Third-party code under static/vendor/ is exempt.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "stagewatch" / "web" / "static"
SINKS = re.compile(r"\b(innerHTML|outerHTML|insertAdjacentHTML|document\s*\.\s*write(ln)?)\b")


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
            if SINKS.search(line):
                hits.append(f"{p.name}:{i}: {line.strip()[:80]}")
    assert not hits, "build elements with SW.h / textContent instead:\n" + "\n".join(hits)


def test_the_check_itself_catches_sinks():
    assert SINKS.search("el.innerHTML = x")
    assert SINKS.search("el.insertAdjacentHTML('beforeend', x)")
    assert SINKS.search("document.write(x)")
    assert not SINKS.search(_code_only("// no innerHTML here\nconst a = 1;"))
