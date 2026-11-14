-- pgTAP: uploaded documents can be stored as extracted text only, and only
-- project writers can add or delete them.
begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;

select plan(9);

insert into auth.users (id, email, raw_user_meta_data, aud, role)
values
  ('00000000-0000-0000-0000-0000000000d1', 'docs@example.com', '{"full_name":"Doc Owner"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000d2', 'dora@example.com', '{"full_name":"Dora Viewer"}', 'authenticated', 'authenticated'),
  ('00000000-0000-0000-0000-0000000000d3', 'dan@example.com', '{"full_name":"Dan Other"}', 'authenticated', 'authenticated');

insert into public.organization_members (org_id, user_id, role)
select org_id, '00000000-0000-0000-0000-0000000000d2', 'viewer'
from public.organization_members where user_id = '00000000-0000-0000-0000-0000000000d1';

insert into public.projects (id, org_id, name, created_by)
select '90000000-0000-0000-0000-000000000001', org_id, 'Docs', user_id
from public.organization_members
where user_id = '00000000-0000-0000-0000-0000000000d1' and role = 'owner';

select lives_ok(
  $$ insert into public.source_documents (project_id, kind, title, content_text, extraction_status)
     values ('90000000-0000-0000-0000-000000000001', 'pdf', 'brief.pdf', 'Scope text', 'succeeded') $$,
  'an uploaded document can keep only its extracted text');

select lives_ok(
  $$ insert into public.source_documents (project_id, kind, title, extraction_status, extraction_error)
     values ('90000000-0000-0000-0000-000000000001', 'audio', 'call.mp3', 'failed', 'No transcription') $$,
  'a failed extraction can be recorded without a file or text');

select throws_ok(
  $$ insert into public.source_documents (project_id, kind, title)
     values ('90000000-0000-0000-0000-000000000001', 'pdf', 'empty.pdf') $$,
  '23514', null, 'a non-text document needs a file, extracted text or a failed status');

select throws_ok(
  $$ insert into public.source_documents (project_id, kind, title, extraction_status, extraction_error)
     values ('90000000-0000-0000-0000-000000000001', 'pdf', 'x.pdf', 'failed', repeat('x', 501)) $$,
  '23514', null, 'extraction errors are short');

-- ---------------------------------------------------------------------
-- As the project's owner
-- ---------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000d1","role":"authenticated"}';

select lives_ok(
  $$ insert into public.source_documents (id, project_id, kind, title, mime_type, size_bytes, content_text, extraction_status)
     values ('91000000-0000-0000-0000-000000000001', '90000000-0000-0000-0000-000000000001', 'docx',
             'notes.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 1234,
             'Kickoff notes', 'succeeded') $$,
  'a project writer can upload a document');

-- ---------------------------------------------------------------------
-- As a viewer in the same org
-- ---------------------------------------------------------------------
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000d2","role":"authenticated"}';

select throws_ok(
  $$ insert into public.source_documents (project_id, kind, title, content_text, extraction_status)
     values ('90000000-0000-0000-0000-000000000001', 'pdf', 'v.pdf', 'x', 'succeeded') $$,
  '42501', null, 'a viewer cannot upload');

delete from public.source_documents where id = '91000000-0000-0000-0000-000000000001';
select is(
  (select count(*)::int from public.source_documents where id = '91000000-0000-0000-0000-000000000001'),
  1, 'a viewer cannot delete a document');

-- ---------------------------------------------------------------------
-- As someone outside the org
-- ---------------------------------------------------------------------
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000d3","role":"authenticated"}';

select is(
  (select count(*)::int from public.source_documents where project_id = '90000000-0000-0000-0000-000000000001'),
  0, 'other tenants see no documents');

-- ---------------------------------------------------------------------
-- Back to the owner
-- ---------------------------------------------------------------------
set local request.jwt.claims = '{"sub":"00000000-0000-0000-0000-0000000000d1","role":"authenticated"}';

delete from public.source_documents where id = '91000000-0000-0000-0000-000000000001';
select is(
  (select count(*)::int from public.source_documents where id = '91000000-0000-0000-0000-000000000001'),
  0, 'a project writer can delete a document');

select * from finish();
rollback;
