"""Shared pytest configuration.

On Windows, prefer a process-private basetemp under the system temp directory so
local runs are not blocked by leftover locked directories under `.tmp_pytest`.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    if sys.platform != "win32":
        return
    if getattr(config.option, "basetemp", None) is not None:
        return
    config.option.basetemp = str(Path(tempfile.mkdtemp(prefix="em26_pytest_")))
