/**
 * The organization context and the `useOrg` hook that reads it.
 *
 * Purpose: Kept apart from `OrgProvider.tsx` so screens can read the user and active organization
 * without importing the provider.
 * How it fits: `OrgProvider` fills the context; the shell, dashboard and create screens read it;
 * layer: auth.
 * Created: 2026-10-01 · Version: 1.0 (NirnAIn demo release)
 */

import { createContext, useContext } from 'react'
import type { Me, OrganizationMembership } from '@/lib/types'

export interface OrgContextValue {
  me: Me
  organizations: OrganizationMembership[]
  /** The organization every org-scoped query uses. */
  org: OrganizationMembership
  /** Switch the active organization (for a future org switcher). */
  selectOrg: (orgId: string) => void
}

export const OrgContext = createContext<OrgContextValue | null>(null)

/** Returns the user and active organization; throws when used outside `<OrgProvider>`. */
export function useOrg(): OrgContextValue {
  const value = useContext(OrgContext)
  if (!value) throw new Error('useOrg must be used inside <OrgProvider>')
  return value
}
