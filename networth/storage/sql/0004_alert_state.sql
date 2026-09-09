-- Task 15: the two facts DESIGN section 11's alerting rules need and the
-- initial `alert` table does not carry.
--
-- raised_source_as_of: a frozen-data alert resolves only when the account's
-- source clock actually advances -- never because a call succeeded, and never
-- because the Item left HEALTHY and the account therefore stopped being
-- classified FROZEN.  Deciding that later requires the clock the alert was
-- raised for, so it is stored with the alert instead of re-derived from the
-- observation history.  It is nullable because only frozen data resolves this
-- way; the writer requires it for that kind and forbids it for the other three.
--
-- one_open_alert_per_subject: section 11's "one alert per item per state entry"
-- is a uniqueness property, so it is enforced where uniqueness cannot be
-- forgotten.  Enforcing it only in the evaluator would leave the rule true of
-- one caller rather than of the table, and the anti-fatigue guarantee is the
-- whole reason a single-channel design stays credible.  The partial index
-- constrains open alerts only: a resolved alert is history, and the same
-- subject entering the same state again must be able to raise a new one.
--
-- The four-kind vocabulary is NOT enforced here.  SQLite cannot add a CHECK to
-- an existing table without rebuilding it, and rebuilding a table another task
-- created, for a vocabulary the only writer already validates, buys less than
-- it risks.  Whether a fifth kind (publication overdue, for `doctor`'s benefit
-- only) may ever be stored is an open question raised on task 15's PR rather
-- than something this migration should decide by construction.

ALTER TABLE alert ADD COLUMN raised_source_as_of TEXT;

CREATE UNIQUE INDEX one_open_alert_per_subject
    ON alert(kind, ifnull(item_id, -1), ifnull(account_id, -1))
    WHERE resolved_at IS NULL;
