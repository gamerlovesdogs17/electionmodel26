# Senate Probability Lab (web)

Internal research UI for the 2026 Midterms Senate model.

```bash
# from repo root: generate artifact first
python -m midterms.cli forecast --method fast

# optional API
python -m midterms.cli serve-api --port 8787

cd web
npm install
npm run dev   # http://127.0.0.1:4317
```

The UI loads `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8787`) and falls back to `public/data/forecast_latest.json`.
