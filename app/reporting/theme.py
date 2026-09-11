"""TEN Capital house style for the PDF: palette and Open Sans registration.

Open Sans static TTFs (SIL OFL) are bundled in ``app/assets/fonts``. If they are missing
the renderer falls back to Helvetica - the report still renders, just off-brand, which is
the right failure mode for a deployment that lost its assets.
"""

from __future__ import annotations

from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.config import FONT_DIR, LOGO_PATH
from app.utils.logging import get_logger

LOGGER = get_logger(__name__)

NAVY = HexColor("#1F3864")
BLUE = HexColor("#2E75B6")
INK = HexColor("#222222")
GREY = HexColor("#555555")
LIGHT_GREY = HexColor("#8A8A8A")
RULE = HexColor("#D9D9D9")
ZEBRA = HexColor("#F4F6F9")
CALLOUT_FILL = HexColor("#EEF3FA")
WHITE = HexColor("#FFFFFF")
WARN = HexColor("#8C1D18")

TIER_COLORS: dict[str, str] = {
    "tier_1": "#2E7D32",
    "tier_2": "#2E75B6",
    "tier_3": "#555555",
    "tier_4": "#B26A00",
    "do_not_target": "#8C1D18",
}
CONFIDENCE_COLORS: dict[str, str] = {"High": "#2E7D32", "Medium": "#B26A00", "Low": "#8C1D18"}

FONT_FILES: dict[str, str] = {
    "OpenSans": "OpenSans-Regular.ttf",
    "OpenSans-SemiBold": "OpenSans-SemiBold.ttf",
    "OpenSans-Bold": "OpenSans-Bold.ttf",
    "OpenSans-Italic": "OpenSans-Italic.ttf",
}
FALLBACKS: dict[str, str] = {
    "OpenSans": "Helvetica",
    "OpenSans-SemiBold": "Helvetica-Bold",
    "OpenSans-Bold": "Helvetica-Bold",
    "OpenSans-Italic": "Helvetica-Oblique",
}

_resolved: dict[str, str] | None = None


def register_fonts() -> dict[str, str]:
    global _resolved
    if _resolved is not None:
        return _resolved
    resolved: dict[str, str] = {}
    for name, filename in FONT_FILES.items():
        path = FONT_DIR / filename
        try:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            resolved[name] = name
        except Exception:  # noqa: BLE001 - a missing/bad font must not stop a report
            LOGGER.warning("Font %s unavailable; using %s", filename, FALLBACKS[name])
            resolved[name] = FALLBACKS[name]
    if resolved["OpenSans"] == "OpenSans":
        try:
            pdfmetrics.registerFontFamily("OpenSans", normal="OpenSans", bold=resolved["OpenSans-Bold"],
                                          italic=resolved["OpenSans-Italic"],
                                          boldItalic=resolved["OpenSans-Bold"])
        except Exception:  # noqa: BLE001
            LOGGER.debug("Could not register the Open Sans family mapping.")
    _resolved = resolved
    return resolved


def font(name: str) -> str:
    return register_fonts().get(name, FALLBACKS.get(name, "Helvetica"))


def using_brand_fonts() -> bool:
    return register_fonts().get("OpenSans") == "OpenSans"


def logo_path():
    return LOGO_PATH if LOGO_PATH.is_file() else None
