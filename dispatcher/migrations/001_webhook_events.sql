-- Webhook events table: single source of truth for the queue
CREATE TABLE IF NOT EXISTS webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payload JSONB NOT NULL,
    target_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'dead')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    next_retry_at TIMESTAMPTZ,
    attempt_count INT NOT NULL DEFAULT 0,
    last_error TEXT
);

-- Index for worker poll: claim pending rows ready for retry
CREATE INDEX IF NOT EXISTS idx_webhook_events_claim
    ON webhook_events (status, next_retry_at)
    WHERE status = 'pending';

-- Index for debugging / listing by time
CREATE INDEX IF NOT EXISTS idx_webhook_events_created_at
    ON webhook_events (created_at);
