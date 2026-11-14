-- =====================================================================
-- Links between rows stay inside one project / organization (SEC-001).
--
-- A dependency's two work packages, a WBS node's parent, owner and scope
-- item, a milestone's work package, a team member and a skill all point at
-- other rows. The old same-project check (private.validate_dependency,
-- private.validate_wbs_parent) ran as the caller: under RLS another
-- workspace's rows are invisible, so "is it in another project?" found
-- nothing and passed, while the foreign key (which ignores RLS) accepted the
-- row. Another workspace could link your work packages, block your own link
-- through unique (predecessor_id, successor_id), and tell real ids from
-- random ones (foreign key error vs success). The other links had no check.
--
-- Now:
--   * every check is a SECURITY DEFINER trigger function with an empty
--     search_path: it sees every row, so RLS can't fool it;
--   * an unknown id and another project's id raise the same error, before
--     the foreign key is checked, so the answer reveals nothing;
--   * the dependencies insert/update policies also require both work
--     packages to be visible to the caller in the dependency's project;
--   * rows that already break the rules are removed (links, team members,
--     skills) or unset (owner, scope item, milestone package).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Clean-up of rows the old checks let through.
-- ---------------------------------------------------------------------
delete from public.dependencies link
where not exists (select 1 from public.wbs_nodes node
                  where node.id = link.predecessor_id and node.project_id = link.project_id)
   or not exists (select 1 from public.wbs_nodes node
                  where node.id = link.successor_id and node.project_id = link.project_id);

update public.milestones milestone
set wbs_node_id = null
where milestone.wbs_node_id is not null
  and not exists (select 1 from public.wbs_nodes node
                  where node.id = milestone.wbs_node_id and node.project_id = milestone.project_id);

update public.wbs_nodes node
set owner_employee_id = null
where node.owner_employee_id is not null
  and not exists (select 1 from public.employees employee
                  join public.projects project on project.org_id = employee.org_id
                  where employee.id = node.owner_employee_id and project.id = node.project_id);

update public.wbs_nodes node
set scope_item_id = null
where node.scope_item_id is not null
  and not exists (select 1 from public.scope_items item
                  where item.id = node.scope_item_id and item.project_id = node.project_id);

delete from public.project_team_members member
where not exists (select 1 from public.employees employee
                  join public.projects project on project.org_id = employee.org_id
                  where employee.id = member.employee_id and project.id = member.project_id);

delete from public.project_skills listed
where not exists (select 1 from public.skills skill
                  join public.projects project on project.org_id = skill.org_id
                  where skill.id = listed.skill_id and project.id = listed.project_id);

delete from public.task_skill_requirements needed
where not exists (select 1 from public.skills skill
                  join public.projects project on project.org_id = skill.org_id
                  join public.wbs_nodes node on node.project_id = project.id
                  where skill.id = needed.skill_id and node.id = needed.wbs_node_id);

delete from public.employee_skills held
where not exists (select 1 from public.skills skill
                  join public.employees employee on employee.org_id = skill.org_id
                  where skill.id = held.skill_id and employee.id = held.employee_id);

-- ---------------------------------------------------------------------
-- Dependencies: same project, no cycles.
-- ---------------------------------------------------------------------
create or replace function private.validate_dependency()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Security rule: both work packages are nodes of the dependency's project. Unknown
  -- ids and other projects' ids get the same error, so it reveals nothing.
  if not exists (select 1 from public.wbs_nodes node
                 where node.id = new.predecessor_id and node.project_id = new.project_id)
     or not exists (select 1 from public.wbs_nodes node
                    where node.id = new.successor_id and node.project_id = new.project_id) then
    raise exception 'Work package not found in this project' using errcode = 'P0001';
  end if;

  -- Adding pred -> succ creates a cycle if succ already reaches pred.
  if exists (
    with recursive reach(node_id) as (
      select link.successor_id from public.dependencies link
      where link.predecessor_id = new.successor_id and link.id <> new.id
      union
      select link.successor_id from public.dependencies link
      join reach on link.predecessor_id = reach.node_id
      where link.id <> new.id
    )
    select 1 from reach where reach.node_id = new.predecessor_id
  ) or new.successor_id = new.predecessor_id then
    raise exception 'Dependency would create a cycle' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

-- Security rule: a writer may only link work packages they can see in this project.
drop policy "dependencies: project writers can insert" on public.dependencies;
create policy "dependencies: project writers can insert"
  on public.dependencies for insert to authenticated
  with check (
    (select private.can_write_project(project_id))
    and exists (select 1 from public.wbs_nodes node
                where node.id = dependencies.predecessor_id
                  and node.project_id = dependencies.project_id)
    and exists (select 1 from public.wbs_nodes node
                where node.id = dependencies.successor_id
                  and node.project_id = dependencies.project_id)
  );

drop policy "dependencies: project writers can update" on public.dependencies;
create policy "dependencies: project writers can update"
  on public.dependencies for update to authenticated
  using ((select private.can_write_project(project_id)))
  with check (
    (select private.can_write_project(project_id))
    and exists (select 1 from public.wbs_nodes node
                where node.id = dependencies.predecessor_id
                  and node.project_id = dependencies.project_id)
    and exists (select 1 from public.wbs_nodes node
                where node.id = dependencies.successor_id
                  and node.project_id = dependencies.project_id)
  );

-- ---------------------------------------------------------------------
-- WBS nodes: parent, owner and scope item.
-- ---------------------------------------------------------------------
create or replace function private.validate_wbs_parent()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  parent_level smallint;
begin
  if new.parent_id is null then
    return new;
  end if;
  -- Security rule: the parent is a node of the same project (same error for unknown ids).
  select parent.level into parent_level
  from public.wbs_nodes parent
  where parent.id = new.parent_id and parent.project_id = new.project_id;
  if not found then
    raise exception 'WBS parent not found in this project' using errcode = 'P0001';
  end if;
  if parent_level <> new.level - 1 then
    raise exception 'WBS node level must be parent level + 1' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create or replace function private.validate_wbs_links()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Security rule: the owner is an employee of the project's organization and the
  -- scope item one of the project's (same error for unknown ids).
  if new.owner_employee_id is not null and not exists (
    select 1 from public.employees employee
    join public.projects project on project.org_id = employee.org_id
    where employee.id = new.owner_employee_id and project.id = new.project_id
  ) then
    raise exception 'Employee not found in this organization' using errcode = 'P0001';
  end if;
  if new.scope_item_id is not null and not exists (
    select 1 from public.scope_items item
    where item.id = new.scope_item_id and item.project_id = new.project_id
  ) then
    raise exception 'Scope item not found in this project' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger wbs_nodes_validate_links
  before insert or update of owner_employee_id, scope_item_id, project_id on public.wbs_nodes
  for each row execute function private.validate_wbs_links();

-- ---------------------------------------------------------------------
-- Milestones: the work package they cover.
-- ---------------------------------------------------------------------
create or replace function private.validate_milestone_node()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  -- Security rule: the work package is a node of the milestone's project.
  if new.wbs_node_id is not null and not exists (
    select 1 from public.wbs_nodes node
    where node.id = new.wbs_node_id and node.project_id = new.project_id
  ) then
    raise exception 'Work package not found in this project' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger milestones_validate_node
  before insert or update of wbs_node_id, project_id on public.milestones
  for each row execute function private.validate_milestone_node();

-- ---------------------------------------------------------------------
-- Team members and skills: the organization's own people and skills.
-- ---------------------------------------------------------------------
create or replace function private.validate_org_links()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  org uuid;
begin
  -- Security rule: people and skills are linked only within their organization
  -- (same error for unknown ids).
  if tg_table_name = 'employee_skills' then
    select employee.org_id into org from public.employees employee where employee.id = new.employee_id;
  elsif tg_table_name = 'task_skill_requirements' then
    select project.org_id into org
    from public.wbs_nodes node join public.projects project on project.id = node.project_id
    where node.id = new.wbs_node_id;
  else
    select project.org_id into org from public.projects project where project.id = new.project_id;
  end if;

  if tg_table_name = 'project_team_members' then
    if not exists (select 1 from public.employees employee
                   where employee.id = new.employee_id and employee.org_id = org) then
      raise exception 'Employee not found in this organization' using errcode = 'P0001';
    end if;
  elsif not exists (select 1 from public.skills skill
                    where skill.id = new.skill_id and skill.org_id = org) then
    raise exception 'Skill not found in this organization' using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger project_team_members_validate_org
  before insert or update of project_id, employee_id on public.project_team_members
  for each row execute function private.validate_org_links();
create trigger project_skills_validate_org
  before insert or update of project_id, skill_id on public.project_skills
  for each row execute function private.validate_org_links();
create trigger task_skill_requirements_validate_org
  before insert or update of wbs_node_id, skill_id on public.task_skill_requirements
  for each row execute function private.validate_org_links();
create trigger employee_skills_validate_org
  before insert or update of employee_id, skill_id on public.employee_skills
  for each row execute function private.validate_org_links();
