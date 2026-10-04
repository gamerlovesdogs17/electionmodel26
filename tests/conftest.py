"""Shared pytest configuration.

Uses a process-private basetemp under the system temp directory so local Windows
runs are not blocked by leftover locked directories under the repo's `.tmp_pytest`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    if getattr(config.option, "basetemp", None) is not None:
        return
    config.option.basetemp = Path(tempfile.mkdtemp(prefix="em26_pytest_"))
