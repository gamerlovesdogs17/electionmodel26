# Production deploy notes (Senate API)

Static research UI remains on GitHub Pages (no auth — public artifact JSON).

## Authenticated forecast API

```bash
# Local
export MIDTERMS_API_KEY=your-secret
export MIDTERMS_CORS_ORIGINS=https://gamerlovesdogs17.github.io
python -m midterms.cli serve-api --host 0.0.0.0 --port 8787

# Docker Compose
export MIDTERMS_API_KEY=your-secret
docker compose up --build

# Fly.io
fly secrets set MIDTERMS_API_KEY=... MIDTERMS_SIGNING_PRIVATE_KEY="..." MIDTERMS_REQUIRE_SIGNING=1
fly deploy
```

Clients call:
`Authorization: Bearer $MIDTERMS_API_KEY` → `GET /forecast/latest`

`/health` stays public so load balancers can probe without a key.

## Signing trust root

```bash
python -m midterms.cli generate-signing-keys
# commit data/manifests/signing_public_key.pem
# keep private key in MIDTERMS_SIGNING_PRIVATE_KEY or data/licensed/ (gitignored)
export MIDTERMS_REQUIRE_SIGNING=1
```

## Historical polls

VoteHub’s API ([docs](https://votehub.com/polls/api/)) covers the **current** cycle only
(`GET /polls`, …) — there is no `/polls/archive`.

Use FiveThirtyEight / ABC News Senate poll mirrors (CC BY) for complete-cycle replay:

```bash
python -m midterms.cli ingest-fte-polls
python -m midterms.cli replay-cycle --all   # fails closed on synthetic for 2020+
python -m midterms.cli validation-report --full
```

Live 2026 polls stay on VoteHub (`seal-votehub-dumps` / `ingest-polls`).
CI may pass `--allow-synthetic` for fixture-only environments.

## Forecast (v0.9)

Production default is **PyMC** hierarchical-t (`--method pymc`). `fast` is CI/degraded only.

```bash
python -m midterms.cli fetch-ratings
python -m midterms.cli fetch-markets
python -m midterms.cli forecast --method pymc
# or: python -m midterms.cli refresh
```

Expert ratings: Wikipedia multi-rater consensus (Cook/IE/Sabato core + extended WH/RCP/DDHQ/…).
Licensed CSV adapter remains optional via `COOK_RATINGS_CSV`. Never commit vendor CSVs.
