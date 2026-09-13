"""Minimal FastAPI surface for the research UI / production API."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from midterms.api.auth import api_key_configured, cors_origins, require_api_key
from midterms.config import ARTIFACTS_DIR, MODEL_VERSION

app = FastAPI(title="Midterms Senate Model API", version=MODEL_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


def _latest_path() -> Path:
    p = ARTIFACTS_DIR / "forecast_latest.json"
    if not p.exists():
        raise HTTPException(404, "No forecast artifact. Run: python -m midterms.cli forecast")
    return p


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "has_forecast": (ARTIFACTS_DIR / "forecast_latest.json").exists(),
        "model_version": MODEL_VERSION,
        "auth_required": api_key_configured(),
    }


@app.get("/forecast/latest", dependencies=[Depends(require_api_key)])
def forecast_latest() -> dict:
    return json.loads(_latest_path().read_text())


@app.get("/forecast/races", dependencies=[Depends(require_api_key)])
def forecast_races() -> list:
    data = json.loads(_latest_path().read_text())
    return data.get("races", [])


@app.get("/forecast/chamber", dependencies=[Depends(require_api_key)])
def forecast_chamber() -> dict:
    data = json.loads(_latest_path().read_text())
    return data.get("chamber", {})
