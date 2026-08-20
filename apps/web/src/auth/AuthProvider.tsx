import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, use, useMemo, type ReactNode } from 'react'
import { api, ApiError } from '../lib/api'
import type { AuthUser } from '../types/api'

interface AuthContextValue {
  user: AuthUser | null
  isLoading: boolean
  error: Error | null
  setAuthenticatedUser: (user: AuthUser) => void
  logout: () => Promise<void>
  retry: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

async function currentUser(): Promise<AuthUser | null> {
  try {
    return await api.auth.me()
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null
    throw error
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const authQuery = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: currentUser,
    retry: 1,
  })

  const value = useMemo<AuthContextValue>(
    () => ({
      user: authQuery.data ?? null,
      isLoading: authQuery.isLoading,
      error: authQuery.error,
      setAuthenticatedUser: (user) => {
        queryClient.setQueryData(['auth', 'me'], user)
      },
      logout: async () => {
        await api.auth.logout()
        queryClient.clear()
        queryClient.setQueryData(['auth', 'me'], null)
      },
      retry: async () => {
        await authQuery.refetch()
      },
    }),
    [authQuery, queryClient],
  )

  return <AuthContext value={value}>{children}</AuthContext>
}

export function useAuth(): AuthContextValue {
  const context = use(AuthContext)
  if (!context) throw new Error('useAuth doit être utilisé dans AuthProvider')
  return context
}
