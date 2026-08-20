import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthPage } from './auth/AuthPage'
import { useAuth } from './auth/AuthProvider'
import { ErrorState, LoadingState } from './components/ui/Feedback'
import { AppShell } from './layout/AppShell'
import { RealtimeProvider } from './realtime/RealtimeProvider'
import { AgentsPage } from './pages/AgentsPage'
import { ChannelsPage } from './pages/ChannelsPage'
import { RunnersPage } from './pages/RunnersPage'
import { SettingsPage } from './pages/SettingsPage'
import { TasksPage } from './pages/TasksPage'
import { TracesPage } from './pages/TracesPage'
import { WorkflowsPage } from './pages/WorkflowsPage'

export function App() {
  const { user, isLoading, error, retry } = useAuth()

  if (isLoading) {
    return <div className="app-loading"><LoadingState label="Connexion au Control Plane…" /></div>
  }
  if (error) {
    return <div className="app-loading"><ErrorState error={error} onRetry={() => void retry()} /></div>
  }
  if (!user) return <AuthPage />

  return (
    <RealtimeProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/channels" replace />} />
          <Route path="channels" element={<ChannelsPage />} />
          <Route path="channels/:channelId" element={<ChannelsPage />} />
          <Route path="agents" element={<AgentsPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="traces" element={<TracesPage />} />
          <Route path="workflows" element={<WorkflowsPage />} />
          <Route path="runners" element={<RunnersPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/channels" replace />} />
        </Route>
      </Routes>
    </RealtimeProvider>
  )
}
