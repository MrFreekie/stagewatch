"""End-of-day show report (phase A: the report core).

Reads a Stagewatch history database read-only and makes a report: sections of plain data
(``build_report``), one printable HTML page (``html.render_html``) and a ZIP of CSV files
(``csv_zip.make_zip``).  Nothing here imports the hub, so a command line tool can make a
report without starting Stagewatch.  Nothing here writes to the database.
"""

from .build import ALL_SECTIONS, build_report
from .csv_zip import make_zip
from .html import render_html
from .query import ReportError, ReportScope, open_readonly

__all__ = ["ALL_SECTIONS", "ReportError", "ReportScope", "build_report", "make_zip", "open_readonly",
           "render_html"]
