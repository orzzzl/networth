-- Task 10: durable scheduling evidence for hourly /item/get and the
-- investments source clock from status.investments.last_successful_update.
-- Both are nullable for an Item that has never been polled or has no observed
-- Investments status. Writers validate UTC ISO-8601 before persistence.

ALTER TABLE item ADD COLUMN last_health_poll_at TEXT;
ALTER TABLE item ADD COLUMN investments_last_successful_update TEXT;
