"""Filesystem helpers that tolerate OneDrive / Windows locks."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd


def safe_to_parquet(df: pd.DataFrame, path: Path | str, *, index: bool = False) -> Path:
    """
    Write parquet via a same-directory temp file, then replace.

    On Windows/OneDrive, direct overwrite often raises OSError errno 22 while
    the cloud client has the file open. If replace fails and an older file
    exists, keep the prior artifact and re-raise only when nothing usable remains.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".parquet.tmp", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        df.to_parquet(tmp, index=index)
        try:
            os.replace(tmp, path)
        except OSError:
            if path.exists() and path.stat().st_size > 0:
                tmp.unlink(missing_ok=True)
                return path
            raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return path
