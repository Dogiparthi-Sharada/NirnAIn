/**
 * AI assistant rules: starter prompts, history limits and the general-guidance fallback reply.
 *
 * Purpose: The assistant must never invent project facts: when it is not configured, not deployed
 * or unreachable, the panel answers with general guidance about where things live in NirnAIn
 * instead of an error (port of the mockup's scripts/views/chat.js).
 * How it fits: Used by `components/AIChat.tsx`, which sends questions through `useAssistant`
 * (`POST /api/v1/assistant/messages`, contract [BE2]) and falls back to `guidanceReply`;
 * layer: hook/lib.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { isApiError } from './api'

/** Starter prompts shown before the first question. */
export const ASSISTANT_SUGGESTIONS = [
  'Summarize project risks',
  'Why is the budget at risk?',
  'Suggest missing deliverables',
  'Explain the optimization result',
] as const

/** The server accepts at most 20 messages of up to 4,000 characters. */
export const MAX_HISTORY = 20
export const MAX_MESSAGE_CHARS = 4000

/** True when the assistant itself is unavailable, so a general-guidance reply is better than an error. */
export function assistantUnavailable(error: unknown): boolean {
  if (!isApiError(error)) return true
  return error.status === 0 || error.status === 404 || error.status === 502 || error.status === 503
}

/** General guidance for `question` on the screen named `context`; no project data involved. */
export function guidanceReply(question: string, context: string): string {
  const text = question.toLowerCase()
  if (text.includes('risk')) {
    return 'Risks AI extracts from your notes are listed in Scope Review, and the risks of an applied plan appear on the Dashboard. Rank them by impact and tie each mitigation to the work packages it affects.'
  }
  if (text.includes('budget') || text.includes('cost')) {
    return 'The planned budget is on the Dashboard and the budget limit is set in Constraints. Once a plan is optimized, compare its total cost with the budget to see the headroom and which constraints drive the cost.'
  }
  if (text.includes('deliverable') || text.includes('wbs')) {
    return 'Check the work breakdown for missing deliverables such as testing, rollout or disaster recovery. AI Validate in the WBS Builder points out structure issues and gaps for you to review.'
  }
  if (text.includes('optim') || text.includes('scenario')) {
    return 'Optimization Results shows how the plan trades cost, schedule and utilization, and which constraints make a plan infeasible. Run the optimizer once the constraints are set to see it.'
  }
  if (text.includes('constraint')) {
    return 'Hard constraints (Must hold, such as a budget ceiling or deadline) can never be broken; soft ones (Can flex, such as skills or capacity) can bend at a cost. Relax the soft ones first when a plan is infeasible.'
  }
  return `I can help with the ${context} step. Try asking about risks, budget, missing deliverables or constraints.`
}
