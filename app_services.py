"""Compatibility facade for application services.

The executable workflow lives in :mod:`app_workflows`; the Gradio view lives
in :mod:`app_ui`.  Keep this module small so existing imports remain valid
while callers move to the focused modules.
"""

from app_ui import build_demo

__all__ = ["build_demo"]
