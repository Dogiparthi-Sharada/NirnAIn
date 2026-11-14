-- =====================================================================
-- Uploaded documents say when only part of their text was kept (INTAKE-020).
--
-- At most 200,000 characters of an upload's text are kept (the rest of a
-- long transcript, PDF or Word file is not read). That used to happen
-- silently; now the upload records it, and the API and the intake screen
-- say "Only the first N characters were read".
--
-- Earlier uploads at the limit were cut the same way, so they are marked too.
-- =====================================================================

alter table public.source_documents
  add column content_truncated boolean not null default false;

comment on column public.source_documents.content_truncated is
  'True when the file had more text than content_text keeps (only its first part was read).';

update public.source_documents
set content_truncated = true
where kind <> 'text'
  and char_length(content_text) >= 200000;
