# -*- coding: utf-8 -*-
"""Shared project paths. core/ lives inside the FocusGuard root, so ROOT is one level up."""
import os
import sys
import json
import platform

if getattr(sys, "frozen", False):
    # Running from a PyInstaller build:
    #   * bundled (read-only) data — e.g. models/ — lives in the unpacked bundle dir;
    #   * user-writable data — config.json, data/ — lives NEXT TO the .exe.
    _BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _BUNDLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _APP_DIR = _BUNDLE_DIR

ROOT = _APP_DIR  # back-compat alias (writable app root)
HERE = ROOT
MODELS_DIR = os.path.join(_BUNDLE_DIR, "models")     # bundled, read-only
SOUNDS_DIR = os.path.join(_BUNDLE_DIR, "sounds")     # bundled, read-only (brown noise)
ASSETS_DIR = os.path.join(_BUNDLE_DIR, "assets")     # bundled, read-only (images)
CONFIG_PATH = os.path.join(_APP_DIR, "config.json")  # writable
DATA_DIR = os.path.join(_APP_DIR, "data")            # writable

IS_WINDOWS = platform.system() == "Windows"

os.makedirs(DATA_DIR, exist_ok=True)


def atomic_write_json(path, obj):
    """Write JSON atomically: temp file in the same dir, then os.replace.

    Prevents a crash / full disk mid-write from leaving a corrupt (half-written)
    config/pet/stats file that would break loading on next launch.
    """
    d = os.path.dirname(path) or "."
    tmp = os.path.join(d, os.path.basename(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
