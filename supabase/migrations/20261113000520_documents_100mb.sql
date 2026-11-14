-- =====================================================================
-- Uploaded intake documents may be up to 100 MB (was 50 MB).
--
-- Long meeting transcripts and scanned briefs regularly exceed 50 MB.
-- The API enforces the same limit (104 857 600 bytes) before reading a
-- file; this check keeps the column consistent with it. The original
-- inline check was named source_documents_size_bytes_check by Postgres.
-- =====================================================================

alter table public.source_documents
  drop constraint if exists source_documents_size_bytes_check,
  add constraint source_documents_size_bytes_check
    check (size_bytes between 0 and 104857600);
