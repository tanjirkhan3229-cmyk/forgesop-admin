/** TanStack Query hooks over the admin API. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  api,
  type PlanInput,
  type UserQuery,
  type WorkspacePatch,
  type WorkspaceQuery,
} from '../lib/api'

export function useOverview() {
  return useQuery({ queryKey: ['overview'], queryFn: api.overview })
}

export function useSignups(range: string) {
  return useQuery({ queryKey: ['signups', range], queryFn: () => api.signups(range) })
}

export function useWorkspaces(q: WorkspaceQuery) {
  return useQuery({
    queryKey: ['workspaces', q],
    queryFn: () => api.workspaces(q),
  })
}

export function useWorkspace(id: string | null) {
  return useQuery({
    queryKey: ['workspace', id],
    queryFn: () => api.workspace(id as string),
    enabled: !!id,
  })
}

export function useUsers(q: UserQuery) {
  return useQuery({ queryKey: ['users', q], queryFn: () => api.users(q) })
}

export function usePlans() {
  return useQuery({ queryKey: ['plans'], queryFn: api.plans })
}

export function useCreatePlan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: PlanInput) => api.createPlan(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['plans'] }),
  })
}

export function useUpdatePlan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ key, body }: { key: string; body: PlanInput }) =>
      api.updatePlan(key, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['plans'] }),
  })
}

/** Apply a plan / set overrides on a workspace. Invalidates workspace + lists. */
export function usePatchWorkspace(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: WorkspacePatch) => api.patchWorkspace(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspace', id] })
      qc.invalidateQueries({ queryKey: ['workspaces'] })
    },
  })
}
