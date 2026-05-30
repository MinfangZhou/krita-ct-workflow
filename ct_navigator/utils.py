"""
Utility functions for CT Navigator
"""

import logging
import os
import tempfile

# Set up plugin logger
logger = logging.getLogger("ct_navigator")
logger.setLevel(logging.DEBUG)

# Debug log to Temp (guaranteed writable in any Windows process)
try:
    _log_path = os.path.join(tempfile.gettempdir(), "ct_nav_debug.log")
    _fh = logging.FileHandler(_log_path, mode="a", encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _fh.setFormatter(_fmt)
    logger.addHandler(_fh)
except Exception as _e:
    pass


def safe_disconnect(signal, slot):
    """Safely disconnect a signal-slot connection, ignoring errors."""
    try:
        signal.disconnect(slot)
    except (TypeError, RuntimeError):
        pass


def get_document_info(doc):
    """Get human-readable document info for debugging."""
    if doc is None:
        return "No document"
    return f"'{doc.name()}' {doc.width()}x{doc.height()} @ {doc.resolution():.0f}dpi"


def has_content(doc):
    """Check if document has any visible content (not blank)."""
    if doc is None:
        return False
    # Check if all pixels are transparent or same color
    # A simple heuristic: check if the projection has variation
    try:
        proj = doc.projection(doc.bounds())
        if proj is None or proj.isNull():
            return False
        # If image is all same color, consider it blank
        # Sample center pixel vs corner pixel
        w, h = proj.width(), proj.height()
        if w > 2 and h > 2:
            corner = proj.pixelColor(0, 0)
            center = proj.pixelColor(w // 2, h // 2)
            if corner == center:
                # Check a few more points
                edge = proj.pixelColor(w - 1, h - 1)
                if edge == corner:
                    return False
        return True
    except Exception:
        return True  # Assume has content if we can't check
