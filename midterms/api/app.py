"""Minimal FastAPI surface for the internal research UI."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from midterms.config import ARTIFACTS_DIR

app = FastAPI(title="Midterms Senate Model API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _latest_path() -> Path:
    p = ARTIFACTS_DIR / "forecast_latest.json"
    if not p.exists():
        raise HTTPException(404, "No forecast artifact. Run: python -m midterms.cli forecast")
    return p


@app.get("/health")
def health() -> dict:
    return {"ok": True, "has_forecast": (ARTIFACTS_DIR / "forecast_latest.json").exists()}


@app.get("/forecast/latest")
def forecast_latest() -> dict:
    return json.loads(_latest_path().read_text())


@app.get("/forecast/races")
def forecast_races() -> list:
    data = json.loads(_latest_path().read_text())
    return data.get("races", [])


@app.get("/forecast/chamber")
def forecast_chamber() -> dict:
    data = json.loads(_latest_path().read_text())
    return data.get("chamber", {})
