"""L-Acoustics Network Manager (LA NWM) log: reading its ``Log_*.xml`` file (parse.py) and deciding what
each entry means (mapping.py). Used by the log import (importer.py). Read-only: Stagewatch only
reads a copy of the file and never talks to LA NWM or the amplifiers.

STATUS: written from one real LA NWM file (V2026.2); severity wording will be tuned as more are seen.
There is no live watcher yet.
"""
