import os
import sys


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
