"""Shared fixtures: synthetic decks are generated fresh into a temp dir for every session."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import MatchingConfig, Settings  # noqa: E402
from app.extraction.company_extractor import extract_company  # noqa: E402
from app.ingestion.deck_parser import parse_deck  # noqa: E402
from app.models.consent import ConsentRecord  # noqa: E402
from app.services.investor_data_service import load_investor_dataset  # noqa: E402

DATASET_PATH = ROOT / "sample_data" / "investors_synthetic.json"


def _make_samples_module():
    spec = importlib.util.spec_from_file_location("make_samples", ROOT / "sample_data" / "make_samples.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _network_blocked(*_args, **_kwargs):
    raise AssertionError("Tests must never send real email - inject a fake transport.")


@pytest.fixture(autouse=True)
def _no_external_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never email anyone or call Claude, whatever the developer's .env contains."""
    monkeypatch.setenv("TEN_NOTIFY_ENABLED", "false")
    monkeypatch.setenv("TEN_LLM_ENABLED", "false")
    monkeypatch.setattr("app.services.notification_service.urllib_transport", _network_blocked)


@pytest.fixture(scope="session")
def samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    builder = _make_samples_module()
    out = tmp_path_factory.mktemp("decks")
    return {
        "pdf": builder.build_pdf(out / "lumora_diagnostics_series_a.pdf"),
        "pptx": builder.build_pptx(out / "lumora_diagnostics_series_a.pptx"),
        "scanned": builder.build_scanned_pdf(out / "scanned_deck.pdf"),
        "sparse": builder.build_sparse_pdf(out / "nimbus_robotics.pdf"),
    }


@pytest.fixture(scope="session")
def lumora_company(samples: dict[str, Path]):
    return extract_company(parse_deck(samples["pdf"])).company


@pytest.fixture(scope="session")
def dataset():
    return load_investor_dataset([DATASET_PATH])


@pytest.fixture
def config() -> MatchingConfig:
    return MatchingConfig.load(ROOT / "config" / "matching.toml")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, llm_enabled=False, investor_data_paths=str(DATASET_PATH),
                    output_dir=tmp_path / "output")


@pytest.fixture
def consent() -> ConsentRecord:
    return ConsentRecord(analysis_consent=True, data_storage_consent=True, source="test")
