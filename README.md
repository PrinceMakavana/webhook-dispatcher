# Webhook Dispatcher — At-Least-Once Delivery

A durable webhook dispatcher with **POST /events** ingestion, a **Postgres-backed queue**, **exponential backoff with jitter**, **HMAC request signing** for receiver verification, and a **chaotic mock receiver**—all runnable via Docker Compose.

## Architecture

- **Ingestion**: `POST /events` → validate → insert into Postgres (`status = pending`) → **202 Accepted**.
- **Dispatch**: A background worker polls Postgres for `pending` rows where `next_retry_at <= now()`, claims them with `SELECT ... FOR UPDATE SKIP LOCKED`, sends an HTTP POST to the target URL with an HMAC-signed body, then either marks `delivered` or sets `next_retry_at` with exponential backoff.
- **Persistence**: Postgres is the single source of truth. No in-memory queue; a process kill only loses the in-flight HTTP call, and that row stays `pending` and is retried after restart.

```
Client → POST /events → Dispatcher API → Postgres
                              ↓
                    Worker polls Postgres
                              ↓
                    HTTP + HMAC → Mock Receiver (chaotic: 70% fail, delays)
```

## How to run

### With Docker Compose (recommended)

```bash
docker compose up --build
```

- **Dispatcher API**: http://localhost:8000 (POST /events, GET /events/{id})
- **Mock receiver**: http://localhost:8080/webhook
- **Postgres**: internal on 5432

### Send an event

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{"payload": {"hello": "world"}, "target_url": "http://mock-receiver:8080/webhook"}'
```

Or omit `target_url` to use the default (set by `TARGET_URL` in the dispatcher env, which in Docker points to the mock receiver):

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{"payload": {"hello": "world"}'
```

You get **202 Accepted** and `{"id": "<uuid>", "status": "accepted"}`. The worker will deliver to the mock receiver; with ~70% failure and backoff you should see retries in the dispatcher logs and eventually success.

### Proving “no loss” and backoff

- **No loss**: Stop the dispatcher (`docker compose stop dispatcher`) while events are pending or after a few failures. Restart with `docker compose up -d`. The same event is retried and eventually delivered (same `id`, increasing `attempt_count` in DB/logs).
- **Backoff**: Mock receiver fails ~70% of the time. Dispatcher logs show retries with increasing delays (~2s, ~4s, ~8s, …) then 200 and `delivered`. See `docs/delivery-proof.log` for an example capture (or run once and save logs).

## Configuration (environment)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/webhook_dispatcher` | Postgres connection string |
| `WEBHOOK_SECRET` | `change-me-in-production` | Shared secret for HMAC signing (dispatcher and receiver must match) |
| `TARGET_URL` | (see code) | Default target URL when not provided in the event body |
| `WORKER_POLL_INTERVAL` | `1.5` | Seconds between worker polls |
| `HTTP_TIMEOUT` | `15` | Timeout for outbound webhook HTTP calls |
| `MAX_ATTEMPTS` | `20` | After this many failures, event is marked `dead` |
| `BACKOFF_BASE_SECONDS` | `2` | Base for exponential backoff (2, 4, 8, … seconds) |

Mock receiver:

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBHOOK_SECRET` | Same as dispatcher | Must match for signature verification |
| `FAILURE_RATE` | `0.7` | Fraction of requests that return 500 |
| `MAX_DELAY_SEC` | `5` | Max random delay before responding |
| `HANG_RATE` | `0.08` | Fraction of requests that hang (client timeout) |

## Project layout

```
├── docker-compose.yml
├── dispatcher/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py              # uvicorn entry
│   ├── app/
│   │   ├── api.py           # FastAPI, POST /events
│   │   ├── db.py            # Postgres connection and queries
│   │   ├── worker.py        # poll, claim, send, backoff
│   │   └── sign.py          # HMAC sign
│   └── migrations/          # SQL for webhook_events, delivery_attempts
├── mock_receiver/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py              # Chaotic server + HMAC verify
└── docs/
    └── delivery-proof.log   # Example logs 
```

## License

MIT.
