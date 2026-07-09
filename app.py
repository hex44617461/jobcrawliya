"""Compatibility entry point for `streamlit run app.py`.

The real Streamlit app lives in `src/app.py`; importing it here keeps the
local and Docker entry points on the same implementation.
"""

from src.app import *  # noqa: F401,F403
