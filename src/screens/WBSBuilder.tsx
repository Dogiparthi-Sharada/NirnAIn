/**
 * Step 4, WBS Builder: review the work breakdown AI drafted from the accepted scope, edit it and confirm every AI estimate.
 *
 * Purpose: The solver must never plan on numbers nobody checked: Continue to Constraints is never
 * disabled, but while a work package's duration is an unconfirmed AI estimate (or missing) a click
 * opens `#wbs-banner` listing them, each linking to its Confirm button, plus "Confirm all". Each
 * work package names the skills it needs (D6); a server without that field hides the picker with a
 * reason. The WBS stays editable after a plan is applied; changes take effect on the next run.
 * Without accepted scope nothing is generated: the empty table sends the PM to Scope Review.
 * How it fits: Port of the mockup's views/wbs.js; route `projects/:projectId/wbs` in `App.tsx`;
 * uses the WBS query hooks, `lib/wbs.ts`, the AI jobs in `wbs/useWbsAiJobs.ts` and the
 * `components/Wbs*` parts; layer: screen.
 * Created: 2026-09-24 · Version: 1.0 (NirnAIn demo release)
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { GapBanner, useBannerFocus, type GapItem } from '@/components/CreateForm'
import { PlanInForceBanner } from '@/components/InputsKit'
import WbsTable, { estimateControlId } from '@/components/WbsTable'
import WbsToolbar from '@/components/WbsToolbar'
import WbsEmptyState from '@/components/WbsEmptyState'
import WbsValidationCard from '@/components/WbsValidationCard'
import { WbsSkillsOffBanner } from '@/components/WbsSkillPicks'
import { errorMessage } from '@/lib/api'
import { useCapacity, useCreateWbsNode, useMoveWbsNode, useProject, useScopeItems, useUpdateWbsNode, useWbs } from '@/lib/queries'
import { toggledIn, withoutValue } from '@/lib/sets'
import { useStepNav } from '@/lib/steps'
import type { WbsNodePatch } from '@/lib/types'
import { displayCode, movePosition, totalWeeks, useConfirmEstimates, useWbsSkills, wbsBlockers, weeksLabel, type WbsTreeNode } from '@/lib/wbs'
import { useWbsAiJobs } from './wbs/useWbsAiJobs'

/** How many open estimates the banner names before "and N more". */
const BANNER_ITEMS = 5

/** The WBS review: tree/list table, estimate confirmation, AI validation and the Constraints gate. */
export default function WBSBuilder() {
  const goToStep = useStepNav()
  const { projectId = '' } = useParams()
  const project = useProject(projectId)
  const wbsQuery = useWbs(projectId)
  const scope = useScopeItems(projectId)
  const team = useCapacity(projectId)
  const createNode = useCreateWbsNode(projectId)
  const updateNode = useUpdateWbsNode(projectId)
  const moveNode = useMoveWbsNode(projectId)
  const confirmEstimates = useConfirmEstimates(projectId)
  const wbsSkills = useWbsSkills(projectId)

  const [flat, setFlat] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<Set<string>>(() => new Set())
  const [tried, setTried] = useState(false)
  const banner = useBannerFocus()

  useEffect(() => {
    document.title = 'WBS Builder · NirnAIn'
  }, [])

  const nodes = useMemo(() => (wbsQuery.data ?? []) as WbsTreeNode[], [wbsQuery.data])
  const root = nodes.find((node) => node.level === 1)
  const deliverables = nodes.filter((node) => node.level === 2)
  const packages = nodes.filter((node) => node.level === 3)
  const blockers = wbsBlockers(nodes)

  const { generateJob, validateJob, startGenerate, startValidate, generateStatus, generating, validating, needsScope } = useWbsAiJobs(
    projectId,
    wbsQuery,
    scope,
    nodes.length,
  )
  const actionError =
    createNode.error ?? updateNode.error ?? moveNode.error ?? wbsSkills.error ?? confirmEstimates.error ?? startGenerate.error ?? startValidate.error

  /** The project row, created on demand when the PM starts a WBS by hand. */
  async function ensureRoot(): Promise<string | null> {
    if (root) return root.id
    try {
      const created = await createNode.mutateAsync({ parentId: null, name: project.data?.name ?? 'Project' })
      return created.id
    } catch {
      return null
    }
  }

  // One add at a time: a double click or a retry while the first is saving must not create a
  // duplicate node (QA PACK-D-DUP-NODE). The ref stops a second click before the re-render.
  const addingRef = useRef(false)
  const adding = createNode.isPending
  /** Adds one node under the parent `parentId` resolves to, ignoring clicks while an add is still saving. */
  function addOnce(parentId: () => Promise<string | null>, name: string) {
    if (addingRef.current) return
    addingRef.current = true
    void parentId()
      .then((id) => (id ? createNode.mutateAsync({ parentId: id, name }) : null))
      .catch(() => null)
      .finally(() => {
        addingRef.current = false
      })
  }

  /** Adds a deliverable under the project row (creating that row first when needed). */
  function addDeliverable() {
    addOnce(ensureRoot, 'New deliverable')
  }

  /** Adds a work package under a deliverable and expands it so the new row shows. */
  function addPackageUnder(parentId: string) {
    addOnce(() => Promise.resolve(parentId), 'New work package')
    setCollapsed((previous) => withoutValue(previous, parentId))
  }

  /** Adds to the selected deliverable (or the selected package's deliverable), else the last one. */
  function addWorkPackage() {
    const selected = nodes.find((node) => node.id === selectedId)
    const parentId = selected?.level === 2 ? selected.id : selected?.level === 3 ? selected.parentId : deliverables.at(-1)?.id
    if (parentId) addPackageUnder(parentId)
  }

  /** Saves a row's fields, then its skills, so the stored node ends up with both. */
  function saveNode(nodeId: string, { skills, ...fields }: WbsNodePatch) {
    updateNode.mutate({ nodeId, patch: fields }, { onSuccess: () => skills && wbsSkills.save({ nodeId, skills }) })
  }

  /** Moves a node to the position of another among its siblings. */
  function moveNodeTo(source: WbsTreeNode, target: WbsTreeNode) {
    const position = movePosition(nodes, source, target)
    if (position !== null) moveNode.mutate({ nodeId: source.id, position })
  }

  /** Collapses or expands a deliverable's work packages. */
  function toggleDeliverable(deliverableId: string) {
    setCollapsed((previous) => toggledIn(previous, deliverableId))
  }

  /** Confirms one AI estimate, showing that row as busy until it is saved. */
  function confirmOne(nodeId: string) {
    setConfirming((previous) => new Set(previous).add(nodeId))
    confirmEstimates.mutate([nodeId], {
      onSettled: () => setConfirming((previous) => withoutValue(previous, nodeId)),
    })
  }

  /**
   * Goes to Constraints when every estimate is confirmed, else opens the gate banner with every row visible.
   */
  function continueToConstraints() {
    setTried(true)
    if (packages.length > 0 && blockers.length === 0) {
      goToStep('constraints')
      return
    }
    setCollapsed(new Set()) // so every linked row is visible
    banner.request()
  }

  /** Scrolls to and focuses a banner link's target: an estimate's Confirm button or "Confirm all". */
  function jumpTo(key: string) {
    const element = document.getElementById(key === 'confirm-all' ? 'confirm-all' : estimateControlId(key))
    element?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    element?.focus({ preventScroll: true })
  }

  const aiEstimates = blockers.filter((node) => node.durationDays != null)
  const gateItems: GapItem[] = blockers.slice(0, BANNER_ITEMS).map((node) => ({
    key: node.id,
    label: `${displayCode(node)} ${node.name}`,
    message: node.durationDays == null ? 'no estimate yet, enter the weeks' : `${weeksLabel(node.durationDays)} (AI estimate)`,
  }))
  if (blockers.length > BANNER_ITEMS) gateItems.push({ key: 'more', label: `and ${blockers.length - BANNER_ITEMS} more`, link: false })
  if (aiEstimates.length > 1) gateItems.push({ key: 'confirm-all', label: `Or confirm all ${aiEstimates.length} at once` })
  const gateLead = `${blockers.length} AI effort estimate${blockers.length > 1 ? 's need' : ' needs'} your confirmation before the solver may use ${blockers.length > 1 ? 'them' : 'it'}:`
  // After a blocked click the banner stays and shrinks live as estimates are confirmed.
  const noPackagesError = tried && packages.length === 0 ? { title: 'Nothing to plan yet', detail: 'Add at least one work package first.' } : null
  const weeks = totalWeeks(nodes)
  const acceptedCount = (scope.data ?? []).filter((item) => item.status === 'accepted').length

  return (
    <section className="view on" id="v-wbs">
      <div className="pg">
        <div className="crumb">
          <Link to="/dashboard">Dashboard</Link> &rsaquo; Create Project &rsaquo; AI Intake &rsaquo; Scope Review &rsaquo; WBS Builder
        </div>
        <div className="hdr">
          <div>
            <h1>WBS Builder</h1>
            <div className="sub">Review and refine the hierarchical Work Breakdown Structure. AI has pre-populated based on scope.</div>
          </div>
          <WbsToolbar
            flat={flat}
            onFlat={setFlat}
            onAddDeliverable={addDeliverable}
            onAddWorkPackage={addWorkPackage}
            canAddWorkPackage={deliverables.length > 0}
            adding={adding}
            onValidate={() => startValidate.mutate()}
            validating={validating}
            canValidate={nodes.length > 0}
          />
        </div>

        <PlanInForceBanner projectId={projectId} id="wbs-in-force" />
        {packages.length > 0 && (
          <div className="banner" id="wbs-src">
            {nodes.some((node) => node.origin === 'ai') ? (
              <>
                AI drafted this WBS from your <b>{acceptedCount || 'accepted'}</b> accepted scope items:{' '}
              </>
            ) : (
              'Your WBS: '
            )}
            {deliverables.length} deliverable{deliverables.length === 1 ? '' : 's'}, {packages.length} work package
            {packages.length === 1 ? '' : 's'}, {weeks} weeks of effort.
          </div>
        )}
        {actionError && (
          <div className="cf-banner bad" role="alert">
            <b>&#9888; That didn&rsquo;t work.</b> {errorMessage(actionError)}
          </div>
        )}
        {wbsSkills.options.unsupported && <WbsSkillsOffBanner />}

        <WbsValidationCard projectId={projectId} hasWbs={nodes.length > 0} job={validateJob.data} starting={startValidate.isPending} onRecheck={() => startValidate.mutate()} />

        <div className="card mt">
          <div className="row between" style={{ marginBottom: 8 }}>
            <h3 style={{ margin: 0 }}>
              Work packages{' '}
              <span className="small muted" style={{ fontWeight: 400 }}>
                AI estimates must be confirmed before the solver uses them.
              </span>
            </h3>
            <button
              className="btn sec sm"
              id="confirm-all"
              type="button"
              onClick={() => confirmEstimates.mutate(undefined)}
              disabled={confirmEstimates.isPending}
              aria-busy={confirmEstimates.isPending || undefined}
              title={confirmEstimates.isPending ? 'Saving the confirmation in progress\u2026' : undefined}
            >
              {confirmEstimates.isPending && confirming.size === 0 ? 'Confirming…' : 'Confirm all estimates'}
            </button>
          </div>
          {root ? (
            <WbsTable
              nodes={nodes}
              flat={flat}
              collapsed={collapsed}
              onToggle={toggleDeliverable}
              owners={(team.data ?? []).map((member) => ({ id: member.employeeId, name: member.fullName }))}
              skillOptions={wbsSkills.options}
              confirming={confirming}
              onConfirm={confirmOne}
              onSave={(node, patch) => saveNode(node.id, patch)}
              onAddChild={(parent) => {
                if (parent.level === 1) addDeliverable()
                else addPackageUnder(parent.id)
              }}
              adding={adding}
              onMove={moveNodeTo}
              onSelect={(node) => setSelectedId(node.id)}
              selectedId={selectedId}
            />
          ) : (
            <WbsEmptyState
              loading={wbsQuery.isPending}
              loadError={wbsQuery.isError ? errorMessage(wbsQuery.error) : null}
              generating={generating}
              needsScope={needsScope}
              generateError={generateStatus === 'failed' ? (generateJob.data?.error ?? 'AI generation failed.') : null}
              onOpenScope={() => goToStep('scope')}
              onRetry={() => startGenerate.mutate()}
            />
          )}
        </div>

        <GapBanner
          id="wbs-banner"
          tone="bad"
          className="mt"
          ref={banner.ref}
          lead={gateLead}
          items={tried ? gateItems : []}
          error={noPackagesError}
          onJump={jumpTo}
        />

        <div className="row between mt">
          <button className="btn sec" type="button" onClick={() => goToStep('scope')}>
            &larr; Back to Scope Review
          </button>
          <div className="row">
            <span className={blockers.length || packages.length === 0 ? 'gate bad' : 'gate'} id="wbs-hint">
              {packages.length === 0
                ? 'Add work packages to continue.'
                : blockers.length
                  ? `Confirm the ${blockers.length} remaining estimate${blockers.length > 1 ? 's' : ''} to continue.`
                  : `${weeks} weeks of effort confirmed by you.`}
            </span>
            <button className="btn" id="btn-to-constraints" type="button" onClick={continueToConstraints}>
              Continue to Constraints &rarr;
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}
