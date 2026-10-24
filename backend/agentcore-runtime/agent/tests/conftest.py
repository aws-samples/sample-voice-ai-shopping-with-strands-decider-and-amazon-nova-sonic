import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def unlimited_turns(request, monkeypatch):
    """Tests run many turns within milliseconds; the per-session turn limit has its own tests."""
    if 'turn_limit' not in request.keywords:
        import turn_pipeline
        monkeypatch.setattr(turn_pipeline, 'TURN_RATE', (1e6, 10**6))


def pytest_configure(config):
    config.addinivalue_line('markers', 'turn_limit: runs with the real per-session turn limit')
