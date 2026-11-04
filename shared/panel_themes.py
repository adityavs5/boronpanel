"""The panel themes a server administrator can choose between.

A theme is one server-wide setting. Every theme ships prebuilt in the release
(its layout, icon set and styles are separate frontend chunks), so choosing one
only stores its id: the browser loads that theme's chunks and no other.
"""
PANEL_THEMES = ("classic", "grid", "modern")
DEFAULT_PANEL_THEME = "modern"


def normalize_panel_theme(value) -> str:
    return value if value in PANEL_THEMES else DEFAULT_PANEL_THEME
