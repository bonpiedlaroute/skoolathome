-- A sheet the parent removed from the list (« Retirer »): hidden on every device, its answers kept
-- for the laptop and the weekly review. The laptop never shows it again.
ALTER TABLE sheets ADD COLUMN hidden_at timestamptz;

-- How long the parent wants a requested sheet to last (5 to 60 minutes). NULL: the default.
ALTER TABLE sheet_requests ADD COLUMN minutes integer CHECK (minutes BETWEEN 5 AND 60);
