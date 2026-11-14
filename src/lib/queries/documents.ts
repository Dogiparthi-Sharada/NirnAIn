/**
 * Query hooks for the source documents uploaded to a project for AI intake.
 *
 * Purpose: Uploads, lists and deletes documents; the server extracts each file's text for the
 * intake.
 * How it fits: Used by `components/DocumentUpload.tsx`, `screens/AIProjectIntake.tsx` and
 * `lib/create.ts`; layer: hook/lib.
 * Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../api'
import type { SourceDocument } from '../types'
import { queryKeys } from './keys'
import { projectPath } from './paths'

/** Uploads one file for AI intake; the server extracts its text. */
export function uploadDocument(projectId: string, file: File): Promise<SourceDocument> {
  const body = new FormData()
  body.append('file', file, file.name)
  return apiFetch<SourceDocument>(projectPath(projectId, '/documents'), { method: 'POST', body })
}

/** Fetches the source documents uploaded to the project. */
export function useDocuments(projectId: string) {
  return useQuery({
    queryKey: queryKeys.documents(projectId),
    queryFn: ({ signal }) => apiFetch<SourceDocument[]>(projectPath(projectId, '/documents'), { signal }),
    enabled: Boolean(projectId),
  })
}

/** Uploads a file and puts it first in the cached document list. */
export function useUploadDocument(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => uploadDocument(projectId, file),
    onSuccess: (uploaded) => {
      queryClient.setQueryData<SourceDocument[]>(queryKeys.documents(projectId), (previous) => [uploaded, ...(previous ?? [])])
    },
  })
}

/** Deletes a document and drops it from the cached list. */
export function useDeleteDocument(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (documentId: string) =>
      apiFetch<void>(`/api/v1/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' }),
    onSuccess: (_void, documentId) => {
      queryClient.setQueryData<SourceDocument[]>(queryKeys.documents(projectId), (previous) =>
        previous?.filter((existing) => existing.id !== documentId),
      )
    },
  })
}
