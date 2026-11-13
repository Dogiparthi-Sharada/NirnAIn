/** Backend contract types (camelCase JSON from `/api/v1`). */

export const PROJECT_TYPES = [
  'technology',
  'construction',
  'product_development',
  'business_process',
  'compliance',
] as const
export type ProjectType = (typeof PROJECT_TYPES)[number]

export const PRIORITIES = ['high', 'medium', 'low'] as const
export type Priority = (typeof PRIORITIES)[number]

export const METHODOLOGIES = ['waterfall', 'agile', 'hybrid', 'kanban'] as const
export type Methodology = (typeof METHODOLOGIES)[number]

export const VISIBILITIES = ['internal', 'client_facing', 'confidential'] as const
export type Visibility = (typeof VISIBILITIES)[number]

export const PROJECT_STATUSES = [
  'draft',
  'planning',
  'on_track',
  'at_risk',
  'delayed',
  'completed',
  'archived',
] as const
export type ProjectStatus = (typeof PROJECT_STATUSES)[number]

export const PROJECT_STEPS = ['create', 'intake', 'scope', 'wbs', 'constraints', 'optimize', 'done'] as const
export type ProjectStep = (typeof PROJECT_STEPS)[number]

export const PROJECT_TYPE_LABELS: Record<ProjectType, string> = {
  technology: 'Technology / IT',
  construction: 'Construction',
  product_development: 'Product Development',
  business_process: 'Business Process',
  compliance: 'Compliance / Regulatory',
}

export const PRIORITY_LABELS: Record<Priority, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

export const METHODOLOGY_LABELS: Record<Methodology, string> = {
  waterfall: 'Waterfall',
  agile: 'Agile / Scrum',
  hybrid: 'Hybrid',
  kanban: 'Kanban',
}

export const VISIBILITY_LABELS: Record<Visibility, string> = {
  internal: 'Internal',
  client_facing: 'Client-Facing',
  confidential: 'Confidential',
}

export const STATUS_LABELS: Record<ProjectStatus, string> = {
  draft: 'Draft',
  planning: 'Planning',
  on_track: 'On Track',
  at_risk: 'At Risk',
  delayed: 'Delayed',
  completed: 'Completed',
  archived: 'Archived',
}

export type OrgRole = 'owner' | 'admin' | 'member' | 'viewer' | (string & {})

export interface OrganizationMembership {
  id: string
  name: string
  slug: string
  role: OrgRole
}

export interface Me {
  id: string
  email: string
  fullName: string | null
  avatarUrl: string | null
  jobTitle: string | null
  organizations: OrganizationMembership[]
}

export interface Project {
  id: string
  orgId: string
  code: string
  name: string
  description: string
  type: ProjectType
  department: string | null
  sponsor: string | null
  pmUserId: string | null
  pmName: string | null
  priority: Priority
  methodology: Methodology
  visibility: Visibility
  /** ISO date `YYYY-MM-DD`; null for drafts that don't have one yet. */
  startDate: string | null
  /** ISO date `YYYY-MM-DD`; null for drafts that don't have one yet. */
  targetEndDate: string | null
  budgetCents: number | null
  currency: string
  status: ProjectStatus
  currentStep: ProjectStep
  progressPct: number
  createdAt: string
  updatedAt: string
}

export interface ProjectCreate {
  name: string
  description: string
  type: ProjectType
  department: string | null
  sponsor: string | null
  pmUserId: string | null
  priority: Priority
  methodology: Methodology
  visibility: Visibility
  startDate: string | null
  targetEndDate: string | null
  budgetCents: number | null
  currency: string
  status: ProjectStatus
  currentStep: ProjectStep
}

export type ProjectUpdate = Partial<ProjectCreate> & { progressPct?: number }

export interface Page<T> {
  items: T[]
  nextCursor: string | null
}

export interface DashboardSummary {
  totalProjects: number
  onTrack: number
  atRiskOrDelayed: number
  totalBudgetCents: number
  createdThisMonth: number
}

export interface Member {
  userId: string
  fullName: string | null
  email: string | null
  role: OrgRole
}

export type NotificationLevel = 'info' | 'warn' | 'alert'

export interface AppNotification {
  id: string
  level: NotificationLevel
  message: string
  projectId: string | null
  readAt: string | null
  createdAt: string
}

/* ───────────────────────────── Optimization ───────────────────────────── */

export type RunStatus = 'queued' | 'running' | 'optimal' | 'feasible' | 'infeasible' | 'timeout' | 'failed'

export interface RunKpi {
  label: string
  value: string
  sub?: string
}

export interface RunResult {
  kpis: RunKpi[]
  reassignments: { task: string; current: string; recommended: string; reason: string }[]
  scheduleChanges: { wbs: string; name: string; original: string; optimized: string; delta: string; reason: string }[]
  utilization: { name: string; before: number; after: number }[]
}

export interface OptimizationRun {
  id: string
  projectId: string
  status: RunStatus
  solver: string | null
  solveSeconds: number | null
  error: string | null
  iis: { name: string; explanation: string }[] | null
  result: RunResult | null
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  appliedAt?: string | null
}

/** What the optimizer needs before it can run (from the 422 problem's `missing` extension). */
export type OptimizerInput = 'workPackages' | 'team'

/* ───────────────────────────── AI Intake / Scope ───────────────────────────── */

export type IntakeRunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface IntakeRun {
  id: string
  projectId: string
  status: IntakeRunStatus
  model: string | null
  itemsExtracted: number | null
  error: string | null
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
}

export const SCOPE_CATEGORIES = [
  'objective',
  'requirement',
  'deliverable',
  'constraint',
  'risk',
  'missing_info',
] as const
export type ScopeCategory = (typeof SCOPE_CATEGORIES)[number]

export const SCOPE_CATEGORY_LABELS: Record<ScopeCategory, string> = {
  objective: 'Objectives',
  requirement: 'Requirements',
  deliverable: 'Deliverables',
  constraint: 'Constraints',
  risk: 'Risks',
  missing_info: 'Missing Information',
}

export type ScopeItemStatus = 'pending' | 'accepted' | 'removed'

export interface ScopeItem {
  id: string
  projectId: string
  category: ScopeCategory
  text: string
  status: ScopeItemStatus
  origin: 'ai' | 'manual'
  confidence: number | null
  sortOrder: number
  createdAt: string
  updatedAt: string
}

export interface ScopeItemPatch {
  text?: string
  status?: ScopeItemStatus
}

export interface ScopeItemCreate {
  category: ScopeCategory
  text: string
}

/* ───────────────────────────── WBS ───────────────────────────── */

export interface WbsSkill {
  skillId: string
  name: string
  minProficiency: number
}

export interface WbsNode {
  id: string
  parentId: string | null
  level: 1 | 2 | 3
  sortOrder: number
  /** Derived from tree position: "1", "1.2", "1.2.3". */
  code: string
  name: string
  description: string | null
  durationDays: number | null
  effortHours: number | null
  ownerEmployeeId: string | null
  ownerName: string | null
  origin: 'ai' | 'manual'
  scopeItemId: string | null
  skills: WbsSkill[]
}

export interface WbsNodeCreate {
  parentId: string | null
  name: string
  durationDays?: number | null
  ownerEmployeeId?: string | null
}

export interface WbsNodePatch {
  name?: string
  durationDays?: number | null
  ownerEmployeeId?: string | null
}

export type AiJobKind = 'wbs_generate' | 'wbs_validate'

export interface AiJob {
  id: string
  projectId: string
  kind: AiJobKind
  status: IntakeRunStatus
  error: string | null
  result: Record<string, unknown> | null
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
}

export type WbsIssueType = 'missing' | 'duplicate' | 'warning'

export interface WbsValidation {
  structureValid: boolean
  levels: number
  issues: { type: WbsIssueType; text: string }[]
}

/* ───────────────────────────── Constraints / resources ───────────────────────────── */

export type ConstraintMode = 'hard' | 'soft'

export interface Milestone {
  id?: string
  name: string
  /** ISO date. */
  dueDate: string
  wbsNodeId: string | null
  mode: ConstraintMode
}

export interface ConstraintSet {
  budgetTotalCents: number | null
  contingencyPct: number
  laborCapCents: number | null
  infraCapCents: number | null
  budgetMode: ConstraintMode
  startDate: string | null
  mustFinishBy: string | null
  deadlineMode: ConstraintMode
  workingDaysPerWeek: number
  capacityMode: ConstraintMode
  skillMode: ConstraintMode
  milestones: Milestone[]
}

export interface TeamMember {
  employeeId: string
  fullName: string
  roleTitle: string | null
  weeklyCapacityHours: number
  /** Average weekly hours already committed to other projects in this project's window. */
  allocatedHours: number
  hourlyRateCents: number | null
  isContractor: boolean
  skills: { skillId: string; name: string; proficiency: number }[]
}

export interface SkillCoverage {
  skillId: string
  name: string
  required: boolean
  available: boolean
}

export type DependencyType = 'FS' | 'SS' | 'FF' | 'SF'

export const DEPENDENCY_TYPE_LABELS: Record<DependencyType, string> = {
  FS: 'Finish-to-Start',
  SS: 'Start-to-Start',
  FF: 'Finish-to-Finish',
  SF: 'Start-to-Finish',
}

export interface Dependency {
  id: string
  predecessorId: string
  successorId: string
  predecessorLabel: string
  successorLabel: string
  type: DependencyType
  lagDays: number
  origin: 'ai' | 'manual'
}

export interface DependencyCreate {
  predecessorId: string
  successorId: string
  type: DependencyType
}

/* ───────────────────────────── AI assistant ───────────────────────────── */

export interface AssistantMessage {
  role: 'user' | 'assistant'
  content: string
}

/* ───────────────────────────── Source documents ───────────────────────────── */

export type DocumentKind = 'text' | 'pdf' | 'docx' | 'transcript' | 'audio'

export interface SourceDocument {
  id: string
  projectId: string
  kind: DocumentKind
  title: string
  mimeType: string | null
  sizeBytes: number | null
  extractionStatus: IntakeRunStatus
  extractionError: string | null
  /** Characters of text extracted for the AI (0 when extraction failed). */
  chars: number
  createdAt: string
}
