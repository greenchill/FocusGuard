# -*- coding: utf-8 -*-
"""
theme.py — the single source of truth for the visual style of the FocusGuard app.

This file collects:
- COLORS: a dictionary of all palette colors (dark theme, "minimalism + retro gaming").
- Spacing constants (SPACING) and corner radii (RADIUS).
- Helper functions neon() and rgba() for convenient color handling in QSS/QPainter.
- setup_fonts(): loads the app's fonts (pixel + regular) with a safe fallback.

IMPORTANT: all other modules import colors FROM HERE, so that the theme can be
recolored in a single place. Do not duplicate hex codes in other files.
"""

from PyQt6.QtGui import QColor, QFont, QFontDatabase

# --------------------------------------------------------------------------- #
# PALETTE. Exact hex values. Dark background + one neon purple accent.         #
# --------------------------------------------------------------------------- #
COLORS = {
    # PASTEL theme: soft beige background, mint + lemon accents, warm dark text.
    # Backgrounds (layered: the "higher" an element sits, the lighter/creamier it is)
    "bg": "#F4EEE0",          # soft warm beige window background
    "surface": "#FCF8EE",     # card surface (light cream)
    "elevated": "#EFE7D4",    # raised elements (hover, active)
    "sidebar": "#ECE3D0",     # sidebar slightly deeper beige

    # Accents
    "accent": "#3DBE9B",      # mint — the main accent
    "accent_soft": "#A6E3CF", # light mint (for gradients/glow)
    "success": "#67C08A",     # soft green — "all good"
    "warning": "#E5B23C",     # lemon — warning
    "danger": "#E47C6F",      # soft coral — bad state (detected issues)

    # Text
    "text": "#433F2E",        # warm dark brown primary text (high contrast on cream)
    "muted": "#8E8674",       # muted warm gray-brown
    "ink": "#2E2B20",         # near-black ink for text/icons ON accent buttons

    # Lines/borders
    "border": "#E0D6C0",      # thin separators and card frames
    "track": "#E7DECA",       # "track" beneath the progress bar/timer ring
}


# --------------------------------------------------------------------------- #
# SIZES. Consistent spacing and radii so the interface stays rhythmic.        #
# --------------------------------------------------------------------------- #
SPACING = {
    "xs": 4,
    "sm": 8,
    "md": 16,
    "lg": 24,
    "xl": 32,
}

RADIUS = {
    "sm": 8,
    "md": 14,
    "lg": 18,
    "pill": 999,  # for "pills" (chips, badges)
}

# Font names that are set after setup_fonts().
# If the pixel font fails to load, both point to the system font.
FONTS = {
    "pixel": "Press Start 2P",  # updated in setup_fonts() if we find another one
    "body": "Segoe UI",
}


# --------------------------------------------------------------------------- #
# HELPER FUNCTIONS FOR COLORS                                                 #
# --------------------------------------------------------------------------- #
def neon(name: str = "accent") -> QColor:
    """Return a QColor by key from the COLORS palette.

    Handy in QPainter code: pen = QPen(neon('accent')).
    If the key is unknown, return the accent color so nothing crashes.
    """
    return QColor(COLORS.get(name, COLORS["accent"]))


def rgba(name: str, alpha: float) -> str:
    """Build an 'rgba(r,g,b,a)' string for use in QSS or in style strings.

    name  — key from COLORS (or a direct hex like '#A855F7').
    alpha — opacity 0.0..1.0.

    Example: rgba('accent', 0.25) -> 'rgba(168, 85, 247, 0.25)'.
    QSS supports rgba(), so this is safe for semi-transparent "glass" panels.
    """
    hex_value = COLORS.get(name, name)
    color = QColor(hex_value)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def qcolor_rgba(name: str, alpha: int) -> QColor:
    """Same as rgba(), but returns a QColor with alpha 0..255 (for QPainter)."""
    color = QColor(COLORS.get(name, name))
    color.setAlpha(alpha)
    return color


# --------------------------------------------------------------------------- #
# FONTS                                                                         #
# --------------------------------------------------------------------------- #
def setup_fonts(app) -> None:
    """Set up the app's fonts.

    We try to find an installed pixel font ('Press Start 2P' / 'VT323').
    If it's not on the system, pixel labels fall back to the regular font
    (the look gets simpler, but the app won't crash — that's the graceful fallback).

    The regular (body) font is taken from the system (Segoe UI on Windows) and set
    as the default font for the whole application.
    """
    # 1) Default base body font for the whole QApplication.
    body_candidates = ["Segoe UI", "Inter", "Helvetica Neue", "Arial"]
    available = set(QFontDatabase.families())
    for fam in body_candidates:
        if fam in available:
            FONTS["body"] = fam
            break
    app.setFont(QFont(FONTS["body"], 10))

    # 2) Pixel font: use the system one if it's installed.
    #    (We don't ship font files, to avoid piling up dependencies/assets.)
    pixel_candidates = ["Press Start 2P", "VT323", "Pixelify Sans", "Silkscreen"]
    chosen_pixel = None
    for fam in pixel_candidates:
        if fam in available:
            chosen_pixel = fam
            break
    # Fallback: if there's no pixel font, use body — it still works.
    FONTS["pixel"] = chosen_pixel if chosen_pixel else FONTS["body"]


def pixel_font(size: int = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Handy constructor for a pixel QFont (for the timer, badges, cat lines)."""
    font = QFont(FONTS["pixel"], size)
    font.setWeight(weight)
    return font


def body_font(size: int = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Handy constructor for a regular QFont (for the main text)."""
    font = QFont(FONTS["body"], size)
    font.setWeight(weight)
    return font
