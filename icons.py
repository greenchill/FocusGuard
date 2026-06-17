# -*- coding: utf-8 -*-
"""
icons.py — crisp vector icons for FocusGuard (no emoji).

The previous UI leaned on OS emoji (🏠 📊 🎁 ⚙️ 🐾 🔥 ▶ ⏸ 🔊 …) which render
inconsistently across machines and look "default". This module renders a small
set of clean, lucide-style line/solid icons from inline SVG via PyQt6.QtSvg,
recolored to any palette color and cached per (name, color, size).

Public API:
    pixmap(name, color, size=22) -> QPixmap   # transparent-bg, hi-dpi aware
    icon(name, color, size=22)   -> QIcon
    dot(color, size=12)          -> QPixmap   # a filled status dot (no emoji)

`color` is a hex string (e.g. "#A855F7") or a key understood by the caller.
Unknown names fall back to a neutral dot so the UI never crashes.
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt, QRectF
from PyQt6.QtGui import QIcon, QPainter, QPixmap, QColor
from PyQt6.QtSvg import QSvgRenderer

# --------------------------------------------------------------------------- #
# Icon library. Each entry is a full <svg> using the literal token COLOR where
# the stroke/fill should be substituted. 24x24 grid, 2px stroke, round joins —
# a consistent line-icon family (home / chart / gear / paw) plus a few solid
# glyphs (play / pause / stop / flame / bolt) for controls and badges.
# --------------------------------------------------------------------------- #
_STROKE = ('fill="none" stroke="COLOR" stroke-width="2" '
           'stroke-linecap="round" stroke-linejoin="round"')

_ICONS = {
    # ---- navigation (line) ------------------------------------------------ #
    "home": f'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M3 10.6 12 3l9 7.6" {_STROKE}/>
        <path d="M5.5 9.5V20h13V9.5" {_STROKE}/>
        <path d="M9.7 20v-5.2h4.6V20" {_STROKE}/></svg>''',
    "chart": f'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M4 20h16" {_STROKE}/>
        <path d="M6.5 20v-6.5" {_STROKE}/>
        <path d="M12 20V7" {_STROKE}/>
        <path d="M17.5 20v-9.5" {_STROKE}/></svg>''',
    "settings": '''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path fill="COLOR" d="M19.14 12.94c.04-.31.06-.63.06-.94s-.02-.63-.06-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96a7.03 7.03 0 0 0-1.62-.94l-.36-2.54A.49.49 0 0 0 13.46 2h-3.84a.49.49 0 0 0-.48.41l-.36 2.54c-.59.24-1.13.56-1.62.94l-2.39-.96a.49.49 0 0 0-.59.22L1.87 8.47a.49.49 0 0 0 .12.61l2.03 1.58c-.04.31-.06.63-.06.94s.02.63.06.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.48-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.49.49 0 0 0-.12-.61l-2.03-1.58zM12 15.6A3.6 3.6 0 1 1 12 8.4a3.6 3.6 0 0 1 0 7.2z"/></svg>''',
    "paw": '''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <ellipse cx="12" cy="16" rx="4.6" ry="3.9" fill="COLOR"/>
        <ellipse cx="6.4" cy="10.4" rx="1.9" ry="2.3" fill="COLOR"/>
        <ellipse cx="10" cy="6.6" rx="1.9" ry="2.4" fill="COLOR"/>
        <ellipse cx="14" cy="6.6" rx="1.9" ry="2.4" fill="COLOR"/>
        <ellipse cx="17.6" cy="10.4" rx="1.9" ry="2.3" fill="COLOR"/></svg>''',

    # ---- timer / actions (solid) ----------------------------------------- #
    "play": '''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M8 5.2c0-.8.86-1.3 1.55-.9l9.1 6.8c.6.45.6 1.35 0 1.8l-9.1 6.8c-.69.4-1.55-.1-1.55-.9z" fill="COLOR"/></svg>''',
    "pause": '''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <rect x="6.5" y="5" width="3.5" height="14" rx="1.4" fill="COLOR"/>
        <rect x="14" y="5" width="3.5" height="14" rx="1.4" fill="COLOR"/></svg>''',
    "stop": '''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <rect x="6.5" y="6.5" width="11" height="11" rx="2.4" fill="COLOR"/></svg>''',
    "target": f'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <circle cx="12" cy="12" r="8.4" {_STROKE}/>
        <circle cx="12" cy="12" r="4.4" {_STROKE}/>
        <circle cx="12" cy="12" r="1.4" fill="COLOR"/></svg>''',
    "volume": f'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M4 9.5h3L11 5.5v13L7 14.5H4z" fill="COLOR" stroke="COLOR" stroke-width="1.4" stroke-linejoin="round"/>
        <path d="M15 9a4.2 4.2 0 0 1 0 6" {_STROKE}/>
        <path d="M17.6 6.6a7.6 7.6 0 0 1 0 10.8" {_STROKE}/></svg>''',
    "lock": f'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <rect x="5" y="10.5" width="14" height="9.5" rx="2.2" fill="COLOR"/>
        <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" {_STROKE}/></svg>''',

    # ---- gamification badges (solid) ------------------------------------- #
    "flame": '''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2.5c1.2 2.6 3.7 3.9 3.7 6.6 0 .9-.3 1.5-.8 2.2.9.4 1.6 1.1 2 2.1a6.4 6.4 0 1 1-12.2 2.6c0-2.4 1.3-3.8 2.6-5 .4 1 .9 1.4 1.7 1.7-.3-2.6.4-4.4 1-5.7.5 1.4 1.6 2 2.4 2.7.1-2.2-1.1-3.9-1.6-5.5z" fill="COLOR"/></svg>''',
    "bolt": '''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M13.2 2.3 4.6 13a1 1 0 0 0 .78 1.63H10l-1.2 7.1a.6.6 0 0 0 1.05.5l8.6-10.7A1 1 0 0 0 17.66 9H13l1.2-6.2a.6.6 0 0 0-1.0-.5z" fill="COLOR"/></svg>''',
    "star": '''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2.6l2.7 5.9 6.3.7-4.7 4.3 1.3 6.2L12 17.8 6.1 19.7l1.3-6.2-4.7-4.3 6.3-.7z" fill="COLOR"/></svg>''',
}


def _svg_pixmap(svg: str, size: int) -> QPixmap:
    """Render an SVG string to a transparent, hi-dpi-aware QPixmap of side `size`."""
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    dpr = 2  # render at 2x for crisp edges on hidpi; downscaled by the device ratio
    pix = QPixmap(size * dpr, size * dpr)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size * dpr, size * dpr))
    painter.end()
    pix.setDevicePixelRatio(dpr)
    return pix


# (name, color, size) -> QPixmap cache (icons are tiny; the cache stays small).
_CACHE: dict = {}


def pixmap(name: str, color: str, size: int = 22) -> QPixmap:
    """Return a recolored icon pixmap. Falls back to a dot for unknown names."""
    key = (name, color, size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    svg = _ICONS.get(name)
    if svg is None:
        pix = dot(color, size)
    else:
        pix = _svg_pixmap(svg.replace("COLOR", color), size)
    _CACHE[key] = pix
    return pix


def icon(name: str, color: str, size: int = 22) -> QIcon:
    """Return a recolored QIcon (for QPushButton.setIcon / nav buttons)."""
    return QIcon(pixmap(name, color, size))


def dot(color: str, size: int = 12) -> QPixmap:
    """A filled status dot (replaces the emoji/●). Transparent background."""
    dpr = 2
    pix = QPixmap(size * dpr, size * dpr)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    m = size * dpr * 0.16
    p.drawEllipse(QRectF(m, m, size * dpr - 2 * m, size * dpr - 2 * m))
    p.end()
    pix.setDevicePixelRatio(dpr)
    return pix
