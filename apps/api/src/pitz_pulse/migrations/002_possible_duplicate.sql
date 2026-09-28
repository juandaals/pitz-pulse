-- Near-duplicate resubmission detection (Spec 06c). Written by complete() in its own
-- transaction; null until a same-language classified candidate matches above the threshold.
ALTER TABLE requests ADD COLUMN possible_duplicate_of TEXT;
