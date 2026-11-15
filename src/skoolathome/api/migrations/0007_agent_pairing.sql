-- `skoolathome pair`: the laptop (NIP-98) creates pairing codes too. No device made them.
ALTER TABLE pairing_tokens ALTER COLUMN created_by DROP NOT NULL;
