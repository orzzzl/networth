-- Task 08: an Item's accounts are read from the provider once, after its Link.
-- NULL means "not asked yet". Without a stored answer, "asked, and none of its
-- accounts is one v0 models" is the same empty account list as "never asked",
-- and that Item would be asked again on every activation, forever.
ALTER TABLE item ADD COLUMN accounts_discovered_at TEXT;
