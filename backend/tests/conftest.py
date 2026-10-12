import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path_factory, monkeypatch):
    """Settings (export folder, GPU tuning) live in the data folder: never read or
    write the real one from tests."""
    monkeypatch.setenv("CHOPCHOP_DATA", str(tmp_path_factory.mktemp("data")))


def pytest_unconfigure(config):
    """Exit without running native library teardown.

    With PyTorch loaded (separation tests), macOS sometimes aborts *after* all tests have
    passed, while C++ static destructors run at interpreter exit ("recursive_mutex lock
    failed"), turning a green run into a crash. Everything is reported by now, so skip
    that teardown and exit with pytest's own status.
    """
    if "torch" in sys.modules:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(config._exitstatus if hasattr(config, "_exitstatus") else 0)


def pytest_sessionfinish(session, exitstatus):
    session.config._exitstatus = int(exitstatus)
