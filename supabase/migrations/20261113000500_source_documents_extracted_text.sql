-- =====================================================================
-- Uploaded intake documents without object storage.
--
-- For now the API extracts the text of an uploaded PDF / Word / transcript
-- file and keeps only that text (content_text); the original file is not
-- stored, so storage_path stays null. Files whose text couldn't be read
-- (audio until transcription exists, scanned PDFs, corrupt files) are
-- recorded with extraction_status = 'failed' and an extraction_error.
--
-- Replaces the original unnamed check (kind = 'text' or storage_path is
-- not null), which Postgres named source_documents_check.
-- =====================================================================

alter table public.source_documents
  drop constraint source_documents_check,
  add constraint source_documents_has_source check (
    kind = 'text'
    or storage_path is not null
    or content_text is not null
    or extraction_status = 'failed'
  ),
  add constraint source_documents_extraction_error_length
    check (char_length(extraction_error) <= 500);

comment on column public.source_documents.content_text is
  'Pasted text, or the text extracted from an uploaded file (the file itself is not stored yet).';
