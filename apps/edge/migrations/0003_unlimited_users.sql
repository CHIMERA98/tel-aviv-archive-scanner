-- Accounts with no weekly quota and no daily cap (the office's own). Set by hand in D1, so no email is in the repo.
ALTER TABLE users ADD COLUMN unlimited INTEGER NOT NULL DEFAULT 0;
