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

For past cycles use FiveThirtyEight’s public Senate poll table (CC BY):

```powershell
python -m midterms.cli ingest-fte-polls
```

Live 2026 polls stay on VoteHub (`seal-votehub-dumps` / `ingest-polls`).

## Ratings (no Cook license required)

**Default:** curated research snapshot + Kalshi overlays. Display ratings are
model-derived from `P(Dem)`. Cook is **not** required.

```powershell
python -m midterms.cli fetch-ratings
python -m midterms.cli fetch-markets
python -m midterms.cli forecast
```

**Optional:** if you hold a vendor license, set `COOK_RATINGS_CSV` / drop a file under
`data/licensed/` and run `ingest-licensed-ratings`. Never commit vendor CSVs.
