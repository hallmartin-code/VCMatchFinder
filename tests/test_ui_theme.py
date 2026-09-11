"""The web UI design: accurate disclosures, self-hosted fonts, and clean copy."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from conftest import ROOT
from PIL import Image
from streamlit.testing.v1 import AppTest

from app.ui import theme

CONFIG = ROOT / ".streamlit" / "config.toml"


def run_app() -> AppTest:
    app = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=60).run()
    assert not app.exception
    return app


def page_text(app: AppTest) -> str:
    return "\n".join(m.value for m in app.markdown)


def test_disclosure_states_the_configured_recipient(monkeypatch):
    monkeypatch.setenv("TEN_NOTIFY_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_dummy_for_tests")
    monkeypatch.setenv("TEN_NOTIFY_TO", "Info@tencapital.group")
    text = page_text(run_app())
    assert "Info@tencapital.group" in text
    assert "gmail" not in text.lower()  # no address from another tool's design copy
    assert "never contacts investors" in text


def test_upload_hint_matches_the_real_limits(monkeypatch):
    monkeypatch.setenv("TEN_MAX_UPLOAD_MB", "50")
    text = page_text(run_app())
    assert "up to 50 MB" in text and ".pptx" in text and ".docx" not in text


def test_no_mojibake_in_ui_copy():
    for path in (ROOT / "app" / "main.py", ROOT / "app" / "ui" / "theme.py"):
        source = path.read_text(encoding="utf-8")
        assert "Â" not in source and "â€" not in source, path.name


def test_fonts_are_self_hosted_and_present():
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["server"]["enableStaticServing"] is True
    faces = config["theme"]["fontFaces"]
    assert {f["family"] for f in faces} == {"Inter", "Sora", "JetBrains Mono"}
    for face in faces:
        assert (ROOT / face["url"]).is_file(), face["url"]
    assert "fonts.googleapis.com" not in (ROOT / "app" / "ui" / "theme.py").read_text(encoding="utf-8")


def test_dollar_amounts_are_not_rendered_as_math():
    # "$1.5M–$3.6M" between two dollar signs would otherwise become LaTeX in Streamlit.
    assert theme.safe_md("write $1.5M–$3.6M into the $6M round") == "write \\$1.5M–\\$3.6M into the \\$6M round"
    assert theme.safe_md(None) == ""


def _static_file(url: str) -> Path:
    assert url.startswith(theme.STATIC_URL + "/"), url
    return theme.STATIC_DIR / url.removeprefix(theme.STATIC_URL + "/")


def test_favicon_is_the_ten_icon_and_every_reference_resolves():
    # The page icon (st.set_page_config) is the TEN icon placed in the public directory.
    assert theme.BRAND_MARK_PNG == theme.STATIC_DIR / "favicon.png"
    with Image.open(theme.BRAND_MARK_PNG) as icon:
        assert icon.size == (512, 512) and icon.mode == "RGBA"

    head_urls = re.findall(r'href="([^"]+)"', theme._HEAD_LINKS)
    assert f"{theme.STATIC_URL}/favicon.png" in head_urls
    assert f"{theme.STATIC_URL}/favicon.ico" in head_urls
    assert f"{theme.STATIC_URL}/site.webmanifest" in head_urls
    assert "favicon.svg" not in theme._HEAD_LINKS  # the old stand-in mark is gone
    for url in head_urls:
        assert _static_file(url).is_file(), url

    manifest = json.loads((theme.STATIC_DIR / "site.webmanifest").read_text(encoding="utf-8"))
    for entry in manifest["icons"]:
        with Image.open(_static_file(entry["src"])) as img:
            assert f"{img.width}x{img.height}" == entry["sizes"], entry["src"]

    brand_src = re.search(r'src="([^"]+)"', theme.BRAND_MARK).group(1)
    assert _static_file(brand_src).is_file()
