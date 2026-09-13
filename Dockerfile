# Production API image (Senate forecast JSON)
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY midterms ./midterms
COPY data/artifacts ./data/artifacts
COPY data/manifests ./data/manifests
COPY data/normalized ./data/normalized

RUN pip install --no-cache-dir -e . fastapi uvicorn cryptography pyarrow pandas numpy scipy

# Auth: set MIDTERMS_API_KEY. Signing: set MIDTERMS_SIGNING_PRIVATE_KEY + MIDTERMS_REQUIRE_SIGNING=1
ENV MIDTERMS_CORS_ORIGINS=""
EXPOSE 8787

CMD ["uvicorn", "midterms.api.app:app", "--host", "0.0.0.0", "--port", "8787"]
