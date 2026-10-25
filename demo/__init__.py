"""Data functions behind the Streamlit demo (app.py). No Streamlit imports here.

Importing this package puts the repo's own `quicklook/src` and `firstcall` first on the path, so the
demo always runs the code in this checkout, and switches off tracing, .env loading and model calls.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "firstcall", ROOT / "quicklook" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

os.environ["QUICKLOOK_TRACING"] = "off"   # no Langfuse traces from the demo
os.environ["QUICKLOOK_OFFLINE"] = "1"     # quicklook.get_parser stays offline; live mode builds its own client
os.environ["FC_NO_DOTENV"] = "1"          # never read a local .env
os.environ["FC_TRACING"] = "0"
