-- pgTAP: uploaded documents may be up to 100 MB, not more.
begin;
create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;

select plan(3);

insert into auth.users (id, email, raw_user_meta_data, aud, role)
values ('00000000-0000-0000-0000-0000000000e1', 'big@example.com', '{"full_name":"Big Files"}', 'authenticated', 'authenticated');

insert into public.projects (id, org_id, name, created_by)
select '90000000-0000-0000-0000-0000000000e1', org_id, 'Big files', user_id
from public.organization_members
where user_id = '00000000-0000-0000-0000-0000000000e1' and role = 'owner';

select lives_ok(
  $$ insert into public.source_documents (project_id, kind, title, size_bytes, content_text, extraction_status)
     values ('90000000-0000-0000-0000-0000000000e1', 'transcript', 'call.vtt', 104857600, 'Hello', 'succeeded') $$,
  'a 100 MB document is accepted');

select throws_ok(
  $$ insert into public.source_documents (project_id, kind, title, size_bytes, content_text, extraction_status)
     values ('90000000-0000-0000-0000-0000000000e1', 'transcript', 'huge.vtt', 104857601, 'Hello', 'succeeded') $$,
  '23514', null, 'a document over 100 MB is rejected');

select throws_ok(
  $$ insert into public.source_documents (project_id, kind, title, size_bytes, content_text, extraction_status)
     values ('90000000-0000-0000-0000-0000000000e1', 'pdf', 'neg.pdf', -1, 'Hello', 'succeeded') $$,
  '23514', null, 'a negative size is rejected');

select * from finish();
rollback;
