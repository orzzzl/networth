-- Task 15: the two facts DESIGN section 11's alerting rules need and the
-- initial `alert` table does not carry.
--
-- raised_source_as_of: a kind whose claim is "stuck at this instant" resolves
-- when that instant advances, so the instant is part of the record instead of
-- something a later resolver re-derives and might get wrong.  Two kinds resolve
-- that way -- frozen data, on the account's source clock, and (task 27) an
-- unconfirmed share count, on the date the owner last confirmed the quantity --
-- and neither resolves because a call succeeded, because anyone looked, or
-- because the Item left HEALTHY and the account therefore stopped being
-- classified FROZEN.  The column is nullable because the other three kinds
-- resolve on a state instead: the writer requires it for those two and forbids
-- it for those three (AlertKind.carries_source_clock).  This paragraph said
-- "only frozen data" and "the other three" until 2026-09-09, which was true of
-- the four-kind vocabulary it was written against.
--
-- one_open_alert_per_subject: section 11's "one open alert per subject per
-- state entry" is a uniqueness property, so it is enforced where uniqueness
-- cannot be forgotten.  Enforcing it only in the evaluator would leave the rule
-- true of one caller rather than of the table, and the anti-fatigue guarantee
-- is the whole reason a single-channel design stays credible.  The partial
-- index constrains open alerts only: a resolved alert is history, and the same
-- subject entering the same state again must be able to raise a new one.  The
-- subject is an Item or an account, which is why the index keys on both columns
-- and why section 11 no longer says "per item".
--
-- The kind vocabulary is NOT enforced here.  SQLite cannot add a CHECK to an
-- existing table without rebuilding it, and rebuilding a table another task
-- created, for a vocabulary the only writer already validates, buys less than
-- it risks.  Task 27 is what that choice bought: it added
-- SHARE_COUNT_UNCONFIRMED with no migration at all, so the count is five and
-- not the four this comment was first written against.  What guards against a
-- careless sixth is therefore a test and not the schema:
--
--     test_there_are_exactly_five_kinds_and_publication_overdue_is_not_one
--
-- That is the cost side of the same choice, and worth knowing before a seventh
-- is proposed.
--
-- Whether *publication overdue* may ever be stored, for `doctor`'s benefit
-- only, is still open: task 15's PR raised it and nothing since has decided it.
-- It is deliberately not the fifth kind -- that slot went to a condition that
-- can actually reach the owner (DESIGN section 11).

ALTER TABLE alert ADD COLUMN raised_source_as_of TEXT;

CREATE UNIQUE INDEX one_open_alert_per_subject
    ON alert(kind, ifnull(item_id, -1), ifnull(account_id, -1))
    WHERE resolved_at IS NULL;
