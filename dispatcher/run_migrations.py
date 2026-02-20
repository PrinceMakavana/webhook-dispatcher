"""Run DB migrations before starting worker and API. Ensures webhook_events exists."""
from app.db import get_session, run_migrations

if __name__ == "__main__":
    with get_session() as session:
        run_migrations(session)
