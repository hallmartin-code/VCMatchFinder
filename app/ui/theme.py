"""TEN Capital design system for the Streamlit app.

Ports the TEN Capital dark-navy design - brand lockup with the tri-colour mark, Sora /
Inter / JetBrains Mono, the gradient-hairline card, the ambient three-point glow - onto
Streamlit, and extends it to the multi-step workspace (stepper, panels, tiles, chips).

**Maintenance note.** Streamlit exposes no public styling API, so these rules reach into
its DOM through ``data-testid`` attributes. Those are internal and can change between
releases, which is why every such selector lives in this one file and ``streamlit`` is
pinned in requirements.txt. If the app renders unstyled after an upgrade, fix this file.

Fonts are self-hosted (``app/static/fonts``, SIL OFL) and registered through
``[[theme.fontFaces]]`` in ``.streamlit/config.toml``: no request goes to a font CDN, which
matters for a tool that handles confidential decks.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from html import escape
from pathlib import Path

import streamlit as st

# Streamlit's static file server types files through `mimetypes`. Slim Linux images (such as
# Railway's) ship no mapping for font files, so they would be served as octet-stream.
for _mime, _ext in (("font/ttf", ".ttf"), ("font/woff2", ".woff2"), ("font/woff", ".woff")):
    mimetypes.add_type(_mime, _ext)

TOKENS: dict[str, str] = {
    "navy_950": "#0B1526",
    "navy_900": "#101E33",
    "navy_800": "#16283F",
    "navy_700": "#1E354F",
    "coral": "#EE5A4E",
    "coral_soft": "#F0776C",
    "amber": "#F3A22A",
    "teal": "#35BEBB",
    "ink_100": "#F3F6FA",
    "ink_300": "#C4D0E0",
    "ink_500": "#7E90A8",
    "ink_600": "#5C6E86",
}

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
#: The TEN Capital icon (512x512 PNG). Every favicon size in ``app/static`` is derived from it.
BRAND_MARK_PNG = STATIC_DIR / "favicon.png"
STATIC_URL = "/app/static"

WORKFLOW_STEPS: tuple[str, ...] = ("Upload", "Matches", "Report")
#: Widget key of the deck uploader; Streamlit adds the class ``st-key-<key>`` to its container,
#: which scopes the dropzone copy to this uploader only.
DECK_UPLOADER_KEY = "deck_upload"


BRAND_MARK = f'<img class="tc-mark" src="{STATIC_URL}/favicon-192.png" alt="TEN Capital">'

_SANS = "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif"
_DISPLAY = "'Sora', 'Inter', system-ui, sans-serif"
_MONO = "'JetBrains Mono', ui-monospace, 'Cascadia Mono', Consolas, monospace"

_CSS = f"""
<style>
:root{{
  --navy-950:#0B1526; --navy-900:#101E33; --navy-800:#16283F; --navy-700:#1E354F;
  --coral:#EE5A4E; --coral-soft:#F0776C; --amber:#F3A22A; --teal:#35BEBB;
  --ink-100:#F3F6FA; --ink-300:#C4D0E0; --ink-500:#7E90A8; --ink-600:#5C6E86;
}}

/* ---- app shell ---------------------------------------------------------- */
.stApp, [data-testid="stAppViewContainer"]{{ background:var(--navy-950); font-family:{_SANS}; }}
/* the ambient tri-colour glow that echoes the three figures in the logo */
[data-testid="stAppViewContainer"]::before{{
  content:""; position:fixed; inset:0; pointer-events:none; z-index:0;
  background:
    radial-gradient(480px 380px at 14% 8%, rgba(238,90,78,.16), transparent 60%),
    radial-gradient(480px 380px at 86% 6%, rgba(243,162,42,.13), transparent 60%),
    radial-gradient(560px 420px at 50% 100%, rgba(53,190,187,.14), transparent 60%);
}}
[data-testid="stHeader"]{{ background:transparent; }}
#MainMenu, footer{{ visibility:hidden; }}
[data-testid="stMainBlockContainer"], .block-container{{
  position:relative; z-index:1; max-width:720px; padding-top:2.6rem; padding-bottom:3rem;
}}
h1,h2,h3,h4{{ font-family:{_DISPLAY}; color:var(--ink-100); }}

/* ---- brand lockup ------------------------------------------------------- */
.tc-brand{{ display:flex; align-items:center; gap:12px; margin:0 0 26px 4px; }}
.tc-mark{{ width:34px; height:34px; flex-shrink:0; }}
.tc-word{{
  font-family:{_DISPLAY}; font-weight:800; font-size:15px; letter-spacing:.04em;
  line-height:1.15; color:var(--ink-100); text-transform:uppercase;
}}
.tc-word span{{
  display:block; font-weight:600; font-size:10px; letter-spacing:.22em;
  color:var(--ink-500); margin-top:2px;
}}
.tc-topbar{{ display:flex; align-items:center; justify-content:space-between; gap:16px;
  flex-wrap:wrap; margin-bottom:18px; }}
.tc-topbar .tc-brand{{ margin:0; }}

/* ---- type ---------------------------------------------------------------- */
.tc-eyebrow{{
  display:flex; align-items:center; gap:8px; font-family:{_MONO}; font-size:11px;
  letter-spacing:.14em; text-transform:uppercase; color:var(--teal); margin-bottom:12px;
}}
.tc-eyebrow::before{{
  content:""; width:6px; height:6px; border-radius:50%; background:var(--teal);
  box-shadow:0 0 0 3px rgba(53,190,187,.18);
}}
.tc-h1{{ font-family:{_DISPLAY}; font-size:28px; font-weight:700; line-height:1.25;
  margin:0 0 12px; letter-spacing:-.01em; color:var(--ink-100);
  word-break:normal; overflow-wrap:normal; hyphens:none; -webkit-hyphens:none; }}
.tc-h1 .arrow{{ color:var(--ink-500); font-weight:400; margin:0 4px; }}
.tc-h1 .to, .tc-h2 .to{{
  background:linear-gradient(90deg,var(--coral-soft),var(--amber));
  -webkit-background-clip:text; background-clip:text; color:transparent;
}}
.tc-h2{{ font-family:{_DISPLAY}; font-size:20px; font-weight:700; line-height:1.3;
  margin:0 0 6px; color:var(--ink-100); }}
.tc-lede{{ color:var(--ink-300); font-size:15px; line-height:1.6; margin:0 0 22px; max-width:52ch; }}
.tc-sub{{ color:var(--ink-500); font-size:13.5px; line-height:1.55; margin:0 0 14px; }}
.tc-label{{ font-family:{_MONO}; font-size:10.5px; letter-spacing:.14em; text-transform:uppercase;
  color:var(--ink-500); margin:10px 0 2px; }}

/* ---- the hero card and workspace panels ---------------------------------- */
/* A markdown <div> cannot wrap later widgets (each element gets its own container),
   so the card is the real Streamlit container, selected through a marker element. */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-card-marker),
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-panel-marker){{
  background:linear-gradient(180deg,var(--navy-900) 0%,var(--navy-800) 100%);
  border:1px solid var(--navy-700); position:relative; align-items:stretch;
  box-shadow:0 30px 60px -20px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.03);
}}
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-card-marker){{
  border-radius:20px; padding:40px 42px 32px; gap:.65rem;
}}
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-panel-marker){{
  border-radius:16px; padding:26px 28px 22px; gap:.55rem; margin-bottom:18px;
}}
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-card-marker)::after,
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-panel-marker.is-active)::after{{
  content:""; position:absolute; top:-1px; left:40px; right:40px; height:2px; border-radius:2px;
  background:linear-gradient(90deg,var(--coral),var(--amber),var(--teal));
}}
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-card-marker) > [data-testid="stElementContainer"],
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-panel-marker) > [data-testid="stElementContainer"]{{
  width:100% !important;
}}
.tc-card-marker, .tc-panel-marker{{ display:none; }}

/* ---- stepper -------------------------------------------------------------- */
.tc-stepper{{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:0 0 20px; }}
.tc-stepitem{{ display:flex; align-items:center; gap:8px; font-family:{_DISPLAY}; font-size:13px;
  font-weight:600; color:var(--ink-600); }}
.tc-stepitem .n{{ width:24px; height:24px; border-radius:50%; display:inline-flex; align-items:center;
  justify-content:center; font-family:{_MONO}; font-size:11px; border:1px solid var(--navy-700);
  background:rgba(255,255,255,.02); color:var(--ink-500); }}
.tc-stepitem.done{{ color:var(--ink-300); }}
.tc-stepitem.done .n{{ background:rgba(53,190,187,.16); border-color:rgba(53,190,187,.5); color:var(--teal); }}
.tc-stepitem.active{{ color:var(--ink-100); }}
.tc-stepitem.active .n{{ border-color:var(--amber); color:var(--amber); box-shadow:0 0 0 3px rgba(243,162,42,.16); }}
.tc-stepline{{ flex:0 1 36px; min-width:14px; height:1px; background:var(--navy-700); }}
.tc-stepline.done{{ background:rgba(53,190,187,.5); }}

/* ---- file uploader as the dropzone ---------------------------------------- */
[data-testid="stFileUploader"] [data-testid="stWidgetLabel"]{{ display:none; }}
[data-testid="stFileUploaderDropzone"]{{
  border:1.5px dashed var(--navy-700) !important; border-radius:14px !important;
  background:rgba(255,255,255,.015) !important; padding:28px 24px !important;
  transition:border-color .18s ease, background .18s ease;
}}
[data-testid="stFileUploaderDropzone"]:hover{{ border-color:var(--teal) !important; background:rgba(53,190,187,.05) !important; }}
[data-testid="stFileUploaderDropzone"] *{{ color:var(--ink-300) !important; }}
/* On the deck uploader only, Streamlit's stock copy is replaced by the design's wording;
   formats and size are interpolated in inject() so the hint can never contradict the real
   limits. Other uploaders (investor CSV/JSON) keep Streamlit's accurate stock text. */
.st-key-{DECK_UPLOADER_KEY} [data-testid="stFileUploaderDropzoneInstructions"] span,
.st-key-{DECK_UPLOADER_KEY} [data-testid="stFileUploaderDropzoneInstructions"] small{{ display:none !important; }}
.st-key-{DECK_UPLOADER_KEY} [data-testid="stFileUploaderDropzoneInstructions"] > div::after{{
  display:block; white-space:pre-line; text-align:left; font-family:{_SANS}; font-size:15px;
  font-weight:600; color:var(--ink-100); line-height:1.55;
}}
[data-testid="stFileUploaderDropzone"] button{{
  background:transparent !important; border:1px solid var(--navy-700) !important;
  color:var(--ink-300) !important; border-radius:8px !important;
}}

/* ---- buttons -------------------------------------------------------------- */
[data-testid="stButton"] > button, [data-testid="stBaseButton-secondary"]{{
  border-radius:12px; border:1px solid var(--navy-700); background:rgba(255,255,255,.02);
  color:var(--ink-300); font-family:{_DISPLAY}; font-weight:600; font-size:14px; padding:10px 18px;
  transition:border-color .15s ease, color .15s ease, transform .15s ease;
}}
[data-testid="stButton"] > button:hover{{ border-color:var(--teal); color:var(--ink-100); }}
[data-testid="stBaseButton-primary"]{{
  border:none !important; color:#17130E !important; font-family:{_DISPLAY};
  background:linear-gradient(90deg,var(--coral) 0%,var(--coral-soft) 45%,var(--amber) 100%) !important;
  font-weight:700 !important; font-size:15px !important; padding:14px 20px !important;
  border-radius:12px !important; box-shadow:0 10px 24px -10px rgba(238,90,78,.45);
  transition:filter .15s ease, transform .15s ease;
}}
[data-testid="stBaseButton-primary"]:hover{{ filter:brightness(1.06); transform:translateY(-1px); }}
[data-testid="stBaseButton-primary"] p{{ color:#17130E !important; font-weight:700 !important; }}
[data-testid="stBaseButton-primary"]:disabled{{ filter:grayscale(.55) brightness(.75); box-shadow:none; transform:none; }}
/* inside the hero card, buttons span the card as in the design */
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-card-marker) [data-testid="stButton"],
[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-card-marker) [data-testid="stButton"] > button{{ width:100%; }}
[data-testid="stDownloadButton"] button{{
  background:rgba(53,190,187,.10) !important; border:1px solid var(--teal) !important;
  color:var(--ink-100) !important; border-radius:12px !important; font-family:{_DISPLAY};
}}

/* ---- inputs, checkboxes, tables, expanders, alerts ------------------------- */
[data-baseweb="select"] > div, [data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input, [data-testid="stTextArea"] textarea{{
  background:var(--navy-950) !important; border:1px solid var(--navy-700) !important;
  color:var(--ink-100) !important; border-radius:10px !important;
}}
[data-testid="stCheckbox"] label p{{ color:var(--ink-300); font-size:14px; }}
[data-testid="stDataFrame"], [data-testid="stDataEditor"]{{ border:1px solid var(--navy-700); border-radius:12px; overflow:hidden; }}
[data-testid="stExpander"] details{{ border:1px solid var(--navy-700) !important; border-radius:12px !important;
  background:rgba(255,255,255,.015); }}
[data-testid="stExpander"] summary p{{ font-family:{_DISPLAY}; font-weight:600; color:var(--ink-100); }}
[data-testid="stAlert"]{{ border-radius:12px; }}
[data-testid="stSidebar"]{{ background:var(--navy-900); border-right:1px solid var(--navy-700); }}
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3{{ font-size:15px; }}
hr{{ border-color:var(--navy-700); }}

/* ---- tiles, chips, disclosure, footer -------------------------------------- */
.tc-tiles{{ display:flex; gap:10px; margin:4px 0 14px; flex-wrap:wrap; }}
.tc-tile{{ flex:1 1 130px; background:rgba(255,255,255,.025); border:1px solid var(--navy-700);
  border-left-width:3px; border-radius:10px; padding:11px 14px; }}
.tc-tile .k{{ font-family:{_MONO}; font-size:9.5px; letter-spacing:.12em; text-transform:uppercase; color:var(--ink-500); }}
.tc-tile .v{{ font-family:{_DISPLAY}; font-weight:700; font-size:20px; margin-top:4px; }}
.tc-chips{{ display:flex; gap:8px; flex-wrap:wrap; }}
.tc-chip{{ font-family:{_MONO}; font-size:10.5px; letter-spacing:.06em; padding:4px 10px; border-radius:999px;
  border:1px solid var(--navy-700); color:var(--ink-500); background:rgba(255,255,255,.02); }}
.tc-chip.on{{ color:var(--teal); border-color:rgba(53,190,187,.45); }}
.tc-chip.warn{{ color:var(--amber); border-color:rgba(243,162,42,.45); }}
.tc-disclosure{{ margin-top:18px; padding-top:16px; border-top:1px solid var(--navy-700);
  font-size:12px; line-height:1.65; color:var(--ink-500); }}
.tc-disclosure p{{ margin:0 0 6px; color:var(--ink-500); font-size:12px; }}
.tc-disclosure code{{ font-family:{_MONO}; background:var(--navy-950); border:1px solid var(--navy-700);
  color:var(--ink-300); padding:2px 6px; border-radius:5px; font-size:11.5px; }}
.tc-footer{{ text-align:center; margin-top:22px; font-family:{_MONO}; font-size:11px;
  letter-spacing:.08em; color:var(--ink-600); text-transform:uppercase; }}

/* ---- analysis progress ----------------------------------------------------- */
.tc-progress{{ display:flex; flex-wrap:wrap; align-items:center; gap:6px 10px; margin:6px 0 4px;
  font-family:{_DISPLAY}; font-size:13.5px; font-weight:600; color:var(--ink-600); }}
.tc-progress .s.done{{ color:var(--teal); }}
.tc-progress .s.now{{ color:var(--ink-100); }}
.tc-progress .s.now::after{{ content:"…"; color:var(--amber); }}
.tc-progress .sep{{ color:var(--ink-600); font-weight:400; }}

/* ---- landing helpers --------------------------------------------------------- */
.tc-hint{{ font-size:13px; color:var(--ink-500); margin:-2px 0 0; text-align:center; }}
.tc-hint.ok{{ color:var(--teal); }}
.tc-oneline{{ margin-top:16px; padding-top:14px; border-top:1px solid var(--navy-700);
  font-size:12.5px; line-height:1.6; color:var(--ink-500); }}

/* ---- company facts ------------------------------------------------------------ */
.tc-missing{{ font-size:13px; color:var(--amber); margin:6px 0 -6px; }}

/* ---- match cards ---------------------------------------------------------------- */
[class*="st-key-tc-match-"]{{ background:rgba(255,255,255,.02); border-color:var(--navy-700) !important;
  border-radius:14px !important; }}
.tc-card-head{{ display:flex; align-items:flex-start; justify-content:space-between; gap:10px; flex-wrap:wrap; }}
.tc-card-name{{ font-family:{_DISPLAY}; font-weight:700; font-size:16.5px; color:var(--ink-100); line-height:1.35; }}
.tc-card-name .rank{{ color:var(--ink-500); font-weight:600; margin-right:6px; }}
.tc-card-meta{{ font-size:12.5px; color:var(--ink-500); margin-top:2px; }}
.tc-badges{{ display:flex; gap:6px; flex-wrap:wrap; }}
.tc-badge{{ font-family:{_MONO}; font-size:10.5px; letter-spacing:.05em; padding:3px 9px; border-radius:999px;
  border:1px solid currentColor; white-space:nowrap; }}
.tc-why{{ color:var(--ink-300); font-size:14px; line-height:1.55; margin:8px 0 4px; }}
.tc-concern{{ color:var(--ink-500); font-size:13px; line-height:1.5; margin:0; }}
.tc-concern b{{ color:var(--coral-soft); font-weight:600; }}
.tc-copy{{ width:100%; border-radius:12px; border:1px solid var(--navy-700); background:rgba(255,255,255,.02);
  color:var(--ink-300); font-family:{_DISPLAY}; font-weight:600; font-size:14px; padding:10px 14px; cursor:pointer; }}
.tc-copy:hover{{ border-color:var(--teal); color:var(--ink-100); }}

/* Streamlit's own markdown heading sizes outrank a plain class, so pin ours. */
[data-testid="stMarkdownContainer"] h1.tc-h1{{ font-size:28px !important; line-height:1.25 !important; padding:0 !important; }}
[data-testid="stMarkdownContainer"] h2.tc-h2{{ font-size:20px !important; line-height:1.3 !important; padding:0 !important; }}
/* the one download in the workspace is the primary action: style it like one */
[data-testid="stDownloadButton"] [data-testid="stBaseButton-primary"]{{
  background:linear-gradient(90deg,var(--coral) 0%,var(--coral-soft) 45%,var(--amber) 100%) !important;
  border:none !important; color:#17130E !important;
}}

/* keyboard users must always see where they are */
:focus-visible{{ outline:2px solid var(--teal) !important; outline-offset:2px; }}
@media (max-width:560px){{
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] > [data-testid="stMarkdown"] .tc-card-marker){{ padding:30px 22px 24px; }}
  [data-testid="stMarkdownContainer"] h1.tc-h1{{ font-size:23px !important; }}
}}
</style>
"""

_HEAD_LINKS = f"""
<link rel="icon" type="image/png" sizes="512x512" href="{STATIC_URL}/favicon.png">
<link rel="icon" type="image/png" sizes="32x32" href="{STATIC_URL}/favicon-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="{STATIC_URL}/favicon-16.png">
<link rel="icon" sizes="any" href="{STATIC_URL}/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="{STATIC_URL}/favicon-180.png">
<link rel="manifest" href="{STATIC_URL}/site.webmanifest">
<meta name="theme-color" content="{TOKENS['navy_950']}">
"""


def safe_md(text: str | None) -> str:
    """Escape data for Streamlit markdown: a pair of ``$`` would otherwise render as LaTeX
    ("$1.5M–$3.6M" becomes math)."""
    return (text or "").replace("$", "\\$")


def _css_string(text: str) -> str:
    """Escape text for a CSS ``content: "..."`` value (``\\A`` is a newline)."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\A ")


def inject(*, wide: bool = False, upload_hint: str = "") -> None:
    """Apply the design system. Safe to call on every rerun."""
    st.markdown(_HEAD_LINKS + _CSS, unsafe_allow_html=True)
    hint = _css_string(upload_hint or "Drag and drop your deck here, or click to browse")
    extra = (f'.st-key-{DECK_UPLOADER_KEY} [data-testid="stFileUploaderDropzoneInstructions"] > div::after'
             f'{{content:"{hint}";}}')
    if wide:
        extra += '[data-testid="stMainBlockContainer"],.block-container{max-width:1360px !important;}'
    st.markdown(f"<style>{extra}</style>", unsafe_allow_html=True)


def brand_lockup() -> str:
    return (f'<div class="tc-brand">{BRAND_MARK}'
            '<div class="tc-word">Ten Capital<span>Network</span></div></div>')


def topbar(chips_html: str = "") -> None:
    st.markdown(f'<div class="tc-topbar">{brand_lockup()}{chips_html}</div>', unsafe_allow_html=True)


@contextmanager
def card(eyebrow: str, heading_html: str, lede: str | None = None) -> Iterator[None]:
    """The hero card; widgets written inside the ``with`` block sit inside it."""
    with st.container():
        lede_html = f'<p class="tc-lede">{escape(lede)}</p>' if lede else ""
        st.markdown(f'<div class="tc-card-marker"></div><div class="tc-eyebrow">{escape(eyebrow)}</div>'
                    f'<h1 class="tc-h1">{heading_html}</h1>{lede_html}', unsafe_allow_html=True)
        yield


@contextmanager
def panel(eyebrow: str, title: str, subtitle: str | None = None, *, active: bool = False) -> Iterator[None]:
    """A workspace panel."""
    with st.container():
        sub = f'<p class="tc-sub">{escape(subtitle)}</p>' if subtitle else ""
        state = " is-active" if active else ""
        st.markdown(f'<div class="tc-panel-marker{state}"></div>'
                    f'<div class="tc-eyebrow">{escape(eyebrow)}</div>'
                    f'<h2 class="tc-h2">{escape(title)}</h2>{sub}', unsafe_allow_html=True)
        yield


def stepper(current: int, completed: int) -> None:
    """``current`` is the 1-based active step; steps up to ``completed`` show as done."""
    parts: list[str] = []
    for index, name in enumerate(WORKFLOW_STEPS, start=1):
        state = "done" if index <= completed and index != current else ("active" if index == current else "")
        mark = "✓" if state == "done" else str(index)
        if index > 1:
            parts.append(f'<span class="tc-stepline{" done" if index <= completed + 1 and index - 1 <= completed else ""}"></span>')
        parts.append(f'<div class="tc-stepitem {state}"><span class="n">{mark}</span><span>{escape(name)}</span></div>')
    st.markdown(f'<div class="tc-stepper" role="list" aria-label="Workflow progress">{"".join(parts)}</div>',
                unsafe_allow_html=True)


def chips_html(items: Iterable[tuple[str, str]]) -> str:
    """items: (text, state) with state in {"on", "warn", "off"}."""
    return '<div class="tc-chips">' + "".join(
        f'<span class="tc-chip {state}">{escape(text)}</span>' for text, state in items) + "</div>"


def tiles(items: Iterable[tuple[str, str, str]]) -> None:
    """items: (label, value, accent colour)."""
    cells = "".join(f'<div class="tc-tile" style="border-left-color:{color}"><div class="k">{escape(k)}</div>'
                    f'<div class="v" style="color:{color}">{escape(v)}</div></div>' for k, v, color in items)
    st.markdown(f'<div class="tc-tiles">{cells}</div>', unsafe_allow_html=True)


def label(text: str) -> None:
    st.markdown(f'<div class="tc-label">{escape(text)}</div>', unsafe_allow_html=True)


def disclosure(paragraphs_html: Iterable[str]) -> None:
    body = "".join(f"<p>{p}</p>" for p in paragraphs_html)
    st.markdown(f'<div class="tc-disclosure">{body}</div>', unsafe_allow_html=True)


def footer(text: str = "Powered by TEN Capital Network") -> None:
    st.markdown(f'<div class="tc-footer">{escape(text)}</div>', unsafe_allow_html=True)


# Streamlit draws icons as ligature text ("upload", "keyboard_arrow_right") that a screen
# reader would announce, and has no option to hide them; this marks each one aria-hidden.
# It also wires the copy buttons, which need the page's clipboard (no Streamlit API).
_PAGE_SCRIPT = """
<script>
(() => {
  if (window.__tcPageScript) return;
  window.__tcPageScript = true;
  const hide = (root) => root.querySelectorAll('[data-testid="stIconMaterial"]:not([aria-hidden])')
    .forEach((el) => el.setAttribute('aria-hidden', 'true'));
  hide(document);
  new MutationObserver(() => hide(document)).observe(document.body, {childList: true, subtree: true});
  document.addEventListener('click', async (event) => {
    const button = event.target.closest('.tc-copy');
    if (!button) return;
    try {
      await navigator.clipboard.writeText(button.dataset.copy || '');
      button.textContent = 'Copied \u2713';
    } catch (_err) {
      button.textContent = 'Copy failed - select the text in See scoring';
    }
    setTimeout(() => { button.textContent = button.dataset.label; }, 2000);
  });
})();
</script>
"""


def page_script() -> None:
    """Install the accessibility and copy-button script (once per page)."""
    st.html(_PAGE_SCRIPT, unsafe_allow_javascript=True)


def progress_html(stages: list[str], current: int) -> str:
    """The analysis stages as one line; stages before ``current`` show as done."""
    parts = []
    for index, text in enumerate(stages):
        state = "done" if index < current else ("now" if index == current else "")
        mark = "✓ " if state == "done" else ""
        parts.append(f'<span class="s {state}">{mark}{escape(text)}</span>')
    arrow = '<span class="sep" aria-hidden="true">→</span>'
    return f'<div class="tc-progress" role="status" aria-live="polite">{arrow.join(parts)}</div>'


def badge(text: str, color: str) -> str:
    return f'<span class="tc-badge" style="color:{color}">{escape(text)}</span>'


def copy_button(label_text: str, text: str) -> None:
    """A button that copies ``text`` to the clipboard (wired by page_script)."""
    # Entities, not raw characters: a blank line would end the markdown HTML block, and a
    # pair of "$" would be read as LaTeX.
    payload = escape(text).replace("\n", "&#10;").replace("$", "&#36;")
    st.markdown(f'<button type="button" class="tc-copy" data-label="{escape(label_text)}" '
                f'data-copy="{payload}">{escape(label_text)}</button>', unsafe_allow_html=True)
