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

## Licensed ratings (Cook etc.)

Never commit vendor CSVs. Place at `data/licensed/cook_senate_ratings.csv` or set
`COOK_RATINGS_CSV`, then `python -m midterms.cli ingest-licensed-ratings`.
