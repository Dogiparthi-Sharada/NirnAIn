/**
 * The Task dependencies card on step 5 (approved mockup views/constraints.js `#deps-body`): which
 * work package must finish (or start) before another, edited in place.
 *
 * Dependencies always hold: the solver never starts a task before the work it depends on, so the
 * card has no Must hold / Can flex switch. Data: `GET/POST …/projects/{id}/dependencies` and
 * `PATCH/DELETE …/dependencies/{id}` (existing endpoints); the picker lists the WBS work packages.
 * Every change refreshes the step 6 confirmation sheet. Read-only while a plan version is in force.
 */
import { useState } from 'react'
import { errorMessage } from '@/lib/api'
import { useInputsRefresh, useSaveFailure } from '@/lib/constraints'
import { useCreateDependency, useDeleteDependency, useDependencies, useUpdateDependency, useWbs } from '@/lib/queries'
import { DEPENDENCY_TYPE_LABELS, type Dependency, type DependencyType } from '@/lib/types'
import { toast } from '@/lib/toast'

const TYPES = Object.keys(DEPENDENCY_TYPE_LABELS) as DependencyType[]
const EMPTY_DRAFT = { predecessorId: '', successorId: '', type: 'FS' as DependencyType }

function typeText(dependency: Dependency): string {
  const lag = dependency.lagDays
  const lagText = lag === 0 ? '' : ` (${lag > 0 ? '+' : ''}${lag} day${Math.abs(lag) === 1 ? '' : 's'} lag)`
  return `${DEPENDENCY_TYPE_LABELS[dependency.type]}${lagText}`
}

function TypeSelect({ id, label, value, onChange }: { id: string; label?: string; value: DependencyType; onChange: (type: DependencyType) => void }) {
  return (
    <select className="in sm" id={id} aria-label={label} value={value} onChange={(event) => onChange(event.target.value as DependencyType)}>
      {TYPES.map((type) => (
        <option key={type} value={type}>
          {DEPENDENCY_TYPE_LABELS[type]}
        </option>
      ))}
    </select>
  )
}

/** The dependencies card. `editable()` returns false (and explains the plan lock) when inputs are frozen. */
export function DependenciesCard({ projectId, editable }: { projectId: string; editable: () => boolean }) {
  const dependenciesQuery = useDependencies(projectId)
  const wbsQuery = useWbs(projectId)
  const createDependency = useCreateDependency(projectId)
  const updateDependency = useUpdateDependency(projectId)
  const deleteDependency = useDeleteDependency(projectId)
  const refresh = useInputsRefresh(projectId)
  const reportFailure = useSaveFailure(projectId)
  const [editing, setEditing] = useState<{ id: string; type: DependencyType } | null>(null)
  const [draft, setDraft] = useState<typeof EMPTY_DRAFT | null>(null)
  const [formError, setFormError] = useState('')

  const dependencies = dependenciesQuery.data ?? []
  const workPackages = (wbsQuery.data ?? []).filter((node) => node.level === 3)

  function startEdit(dependency: Dependency) {
    if (editable()) setEditing({ id: dependency.id, type: dependency.type })
  }

  function saveEdit(dependency: Dependency) {
    if (!editing || !editable()) return
    if (editing.type === dependency.type) {
      setEditing(null)
      return
    }
    updateDependency.mutate(
      { dependencyId: dependency.id, type: editing.type },
      {
        onSuccess: () => {
          setEditing(null)
          refresh()
        },
        onError: reportFailure,
      },
    )
  }

  function remove(dependency: Dependency) {
    if (!editable()) return
    deleteDependency.mutate(dependency.id, {
      onSuccess: () => {
        toast('Dependency removed.')
        refresh()
      },
      onError: reportFailure,
    })
  }

  function openForm() {
    if (!editable()) return
    setFormError('')
    setDraft(EMPTY_DRAFT)
  }

  function add() {
    if (!draft || !editable()) return
    let problem = ''
    if (!draft.predecessorId || !draft.successorId) problem = 'Pick the work that comes first and the work that depends on it.'
    else if (draft.predecessorId === draft.successorId) problem = 'A task cannot depend on itself.'
    else if (dependencies.some((dependency) => dependency.predecessorId === draft.predecessorId && dependency.successorId === draft.successorId)) {
      problem = 'That dependency is already in the list.'
    }
    setFormError(problem)
    if (problem) return
    createDependency.mutate(draft, {
      onSuccess: () => {
        setDraft(null)
        refresh()
      },
      onError: (error) => {
        setFormError(errorMessage(error))
        refresh(error)
      },
    })
  }

  let body
  if (dependenciesQuery.isPending) {
    body = (
      <tr>
        <td colSpan={4} className="small muted" role="status">
          Loading the dependencies&hellip;
        </td>
      </tr>
    )
  } else if (dependenciesQuery.isError) {
    body = (
      <tr>
        <td colSpan={4} className="small" role="alert">
          Couldn&rsquo;t load the dependencies. {errorMessage(dependenciesQuery.error)}{' '}
          <button className="btn sec sm" type="button" onClick={() => void dependenciesQuery.refetch()}>
            Try again
          </button>
        </td>
      </tr>
    )
  } else if (dependencies.length === 0) {
    body = (
      <tr>
        <td colSpan={4} className="small muted">
          No dependencies yet. Add the order in which work must happen.
        </td>
      </tr>
    )
  } else {
    body = dependencies.map((dependency) => {
      const isEditing = editing?.id === dependency.id
      return (
        <tr key={dependency.id}>
          <td>{dependency.predecessorLabel}</td>
          <td>{dependency.successorLabel}</td>
          <td>
            {isEditing ? (
              <TypeSelect id={`dep-type-${dependency.id}`} label="Dependency type" value={editing.type} onChange={(type) => setEditing({ id: dependency.id, type })} />
            ) : (
              typeText(dependency)
            )}
          </td>
          <td className="row-acts">
            {isEditing ? (
              <>
                <button className="btn sm" type="button" onClick={() => saveEdit(dependency)} disabled={updateDependency.isPending}>
                  Save
                </button>{' '}
                <button className="btn sec sm" type="button" onClick={() => setEditing(null)}>
                  Cancel
                </button>
              </>
            ) : (
              <>
                <button className="btn sec sm" type="button" onClick={() => startEdit(dependency)} aria-label={`Edit ${dependency.predecessorLabel} to ${dependency.successorLabel}`}>
                  Edit
                </button>{' '}
                <button className="btn sec sm danger" type="button" onClick={() => remove(dependency)} aria-label={`Remove ${dependency.predecessorLabel} to ${dependency.successorLabel}`}>
                  Remove
                </button>
              </>
            )}
          </td>
        </tr>
      )
    })
  }

  const packageOptions = workPackages.map((node) => (
    <option key={node.id} value={node.id}>
      {node.code} {node.name}
    </option>
  ))

  return (
    <div className="card span2" id="con-deps">
      <div className="card-head">
        <h3>Task dependencies</h3>
        <span className="pol-fixed" title="A task cannot start before the work it depends on">
          Always must hold
        </span>
      </div>
      <table className="tbl">
        <thead>
          <tr>
            <th>Predecessor</th>
            <th>Successor</th>
            <th>Dependency Type</th>
            <th aria-label="Actions" />
          </tr>
        </thead>
        <tbody id="deps-body">{body}</tbody>
      </table>
      {draft ? (
        <div className="member-form" id="dep-form">
          <h4>Add a dependency</h4>
          <div className="alert bad" role="alert" hidden={!formError}>
            {formError}
          </div>
          {wbsQuery.isPending ? (
            <p className="small muted">Loading the work packages&hellip;</p>
          ) : workPackages.length === 0 ? (
            <p className="small muted">Add work packages on step 4 first.</p>
          ) : (
            <div className="g3">
              <div className="field">
                <label htmlFor="dep-predecessor">Predecessor (comes first)</label>
                <select className="in" id="dep-predecessor" value={draft.predecessorId} onChange={(event) => setDraft({ ...draft, predecessorId: event.target.value })}>
                  <option value="">Pick a work package</option>
                  {packageOptions}
                </select>
              </div>
              <div className="field">
                <label htmlFor="dep-successor">Successor (depends on it)</label>
                <select className="in" id="dep-successor" value={draft.successorId} onChange={(event) => setDraft({ ...draft, successorId: event.target.value })}>
                  <option value="">Pick a work package</option>
                  {packageOptions}
                </select>
              </div>
              <div className="field">
                <label htmlFor="dep-new-type">Dependency Type</label>
                <TypeSelect id="dep-new-type" value={draft.type} onChange={(type) => setDraft({ ...draft, type })} />
              </div>
            </div>
          )}
          <div className="row mt">
            <button className="btn sm" type="button" onClick={add} disabled={createDependency.isPending || workPackages.length === 0}>
              {createDependency.isPending ? 'Adding\u2026' : 'Add dependency'}
            </button>
            <button className="btn sec sm" type="button" onClick={() => setDraft(null)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button className="btn sec sm mt" id="btn-add-dep" type="button" onClick={openForm}>
          + Add Dependency
        </button>
      )}
    </div>
  )
}
