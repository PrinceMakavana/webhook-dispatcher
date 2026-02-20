FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Run migrations first so webhook_events exists before worker polls; then worker + API
CMD python run_migrations.py && python -m app.worker & exec uvicorn app.api:app --host 0.0.0.0 --port 8000
