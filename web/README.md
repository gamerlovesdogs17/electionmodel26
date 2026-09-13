# Senate Probability Lab (web)

Internal research UI for the 2026 Midterms Senate model.

```bash
# from repo root: generate artifact first
python -m midterms.cli forecast --method pymc

# optional API
python -m midterms.cli serve-api --port 8787

cd web
npm install
npm run dev   # http://127.0.0.1:4317
```

Locally, set `NEXT_PUBLIC_API_URL` (e.g. `http://127.0.0.1:8787`) to hit the API; otherwise the UI loads `public/data/forecast_latest.json`.

GitHub Pages builds with `GITHUB_PAGES=true` (see `next.config.ts` + `.github/workflows/deploy-pages.yml`) so assets resolve under `/electionmodel26/`.
