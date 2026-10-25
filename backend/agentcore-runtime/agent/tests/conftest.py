import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def user_pool_settings(monkeypatch):
    """The session host always needs its user pool settings (set by the runtime stack)."""
    monkeypatch.setenv('USER_POOL_ID', 'us-east-1_Pool')
    monkeypatch.setenv('USER_POOL_CLIENT_ID', 'client-1')
