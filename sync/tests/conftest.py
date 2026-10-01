from __future__ import annotations

from pathlib import Path

import pytest

from umbrel_edge.models import AccessSettings, EdgeConfig

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def app_data() -> Path:
    return FIXTURES / "app-data"


@pytest.fixture
def config() -> EdgeConfig:
    return EdgeConfig(
        domain="bebitwise.dev",
        proxy_ip="192.168.10.4",
        access=AccessSettings(allowed_emails=["me@example.com"]),
    )
