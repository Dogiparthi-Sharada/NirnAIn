/**
 * Query hook for the AI assistant's replies.
 *
 * Purpose: Sends the conversation and the current screen to the assistant endpoint; the panel
 * decides how to show a failure.
 * How it fits: Used by `components/AIChat.tsx`; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation } from '@tanstack/react-query'
import { apiFetch } from '../api'
import type { AssistantMessage } from '../types'

/** Sends the conversation so far to the AI assistant and resolves to its reply. */
export function useAssistant() {
  return useMutation({
    mutationFn: (body: { projectId?: string; page: string; messages: AssistantMessage[] }) =>
      apiFetch<{ reply: string }>('/api/v1/assistant/messages', { method: 'POST', json: body }),
  })
}
