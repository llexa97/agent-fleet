import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Boxes, CircleGauge, Filter, Plus, Search, ShieldCheck, Workflow } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { PageHeader } from '../components/PageHeader'
import { Button } from '../components/ui/Button'
import { Dialog } from '../components/ui/Dialog'
import { EmptyState, ErrorState, InlineError, LoadingState } from '../components/ui/Feedback'
import { StatusBadge } from '../components/ui/StatusBadge'
import { api } from '../lib/api'
import { formatMoney, initials } from '../lib/format'
import type { Space, Workspace } from '../types/api'

function CreateAgentDialog({ open, onOpenChange, spaces }: { open: boolean; onOpenChange: (open: boolean) => void; spaces: Space[] }) {
  const queryClient = useQueryClient()
  const [spaceId, setSpaceId] = useState('')
  const [handle, setHandle] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState('')
  const [instructions, setInstructions] = useState('')
  const [harness, setHarness] = useState('codex')
  const [workerId, setWorkerId] = useState('')
  const [workspaceId, setWorkspaceId] = useState('')
  const [channelId, setChannelId] = useState('')
  const workersQuery = useQuery({ queryKey: ['workers'], queryFn: api.workers.list, enabled: open })
  const channelsQuery = useQuery({ queryKey: ['channels', 'all'], queryFn: () => api.channels.list(), enabled: open })
  const workspacesQuery = useQuery({ queryKey: ['workspaces', workerId], queryFn: () => api.workspaces.list(workerId || undefined), enabled: open && Boolean(workerId) })
  const availableWorkspaces = (workspacesQuery.data ?? []).filter((workspace) =>
    workspace.status === 'available' && (workspace.space_id === null || workspace.space_id === spaceId),
  )
  const selectedWorkspace = availableWorkspaces.find((workspace) => workspace.id === workspaceId)
  const mutation = useMutation({
    mutationFn: async () => {
      if (!workerId || !workspaceId || !selectedWorkspace) {
        throw new Error('Choisissez un worker et un workspace disponibles')
      }
      if (selectedWorkspace.space_id === null) {
        await api.workspaces.assignSpace(selectedWorkspace.id, spaceId)
      }
      const agent = await api.agents.create({
        space_id: spaceId,
        handle,
        display_name: displayName,
        role,
        instructions,
        runtime: {
          harness,
          worker_id: workerId,
          workspace_id: workspaceId,
          runner_labels: [],
        },
        max_concurrency: 1,
        budget_policy: { max_cost_per_trace: 5, max_turns_per_trace: 30 },
        delegation_policy: { allowed_agents: [] },
      })
      if (channelId) await api.agents.addMembership(agent.id, channelId)
      return agent
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['agents'] })
      onOpenChange(false)
      setHandle(''); setDisplayName(''); setRole(''); setInstructions('')
    },
  })
  const submit = (event: FormEvent) => { event.preventDefault(); mutation.mutate() }
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Créer un agent logique" description="L’identité reste indépendante du harness et du worker choisis.">
      <form className="form-stack" onSubmit={submit}>
        <div className="form-grid">
          <label>Espace<select required value={spaceId} onChange={(event) => { setSpaceId(event.target.value); setChannelId(''); setWorkspaceId('') }}><option value="" disabled>Choisir…</option>{spaces.map((space) => <option key={space.id} value={space.id}>{space.name}</option>)}</select></label>
          <label>Handle<input required pattern="[a-z0-9][a-z0-9-]*" placeholder="backend-dev" value={handle} onChange={(event) => setHandle(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))} /></label>
        </div>
        <label>Nom affiché<input required placeholder="Backend Developer" value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
        <label>Rôle<input required placeholder="Développement des APIs et services" value={role} onChange={(event) => setRole(event.target.value)} /></label>
        <label>Instructions<textarea rows={4} value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="Responsabilités, règles de travail et critères de sortie…" /></label>
        <div className="form-grid">
          <label>Harness<select value={harness} onChange={(event) => setHarness(event.target.value)}><option value="codex">Codex ACP</option><option value="claude">Claude Agent ACP</option><option value="opencode">OpenCode ACP</option></select></label>
          <label>Worker<select required value={workerId} onChange={(event) => { setWorkerId(event.target.value); setWorkspaceId('') }}><option value="">Choisir un worker…</option>{workersQuery.data?.map((worker) => <option key={worker.id} value={worker.id}>{worker.name} · {worker.status}</option>)}</select></label>
        </div>
        <div className="form-grid">
          <label>Workspace<select required value={workspaceId} disabled={!workerId || !spaceId} onChange={(event) => setWorkspaceId(event.target.value)}><option value="">Choisir un workspace…</option>{availableWorkspaces.map((workspace: Workspace) => <option key={workspace.id} value={workspace.id}>{workspace.display_name}{workspace.space_id === null ? ' · sera associé à cet espace' : ''}{workspace.read_only ? ' · lecture seule' : ''}</option>)}</select></label>
          <label>Ajouter au channel<select value={channelId} disabled={!spaceId} onChange={(event) => setChannelId(event.target.value)}><option value="">Plus tard</option>{channelsQuery.data?.filter((channel) => channel.space_id === spaceId).map((channel) => <option key={channel.id} value={channel.id}>#{channel.name}</option>)}</select></label>
        </div>
        <InlineError error={mutation.error} />
        <div className="dialog-actions"><Button variant="secondary" onClick={() => onOpenChange(false)}>Annuler</Button><Button type="submit" isLoading={mutation.isPending}>Créer l’agent</Button></div>
      </form>
    </Dialog>
  )
}

export function AgentsPage() {
  const [createOpen, setCreateOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const spacesQuery = useQuery({ queryKey: ['spaces'], queryFn: api.spaces.list })
  const agentsQuery = useQuery({ queryKey: ['agents'], queryFn: () => api.agents.list() })
  const filtered = useMemo(() => (agentsQuery.data ?? []).filter((agent) => {
    const needle = search.toLowerCase()
    return (status === 'all' || agent.status === status) && (!needle || agent.handle.includes(needle) || agent.display_name.toLowerCase().includes(needle) || agent.role.toLowerCase().includes(needle))
  }), [agentsQuery.data, search, status])
  const active = agentsQuery.data?.filter((agent) => agent.status === 'active' || agent.status === 'busy').length ?? 0

  return (
    <div className="standard-page">
      <PageHeader eyebrow="Flotte logique" title="Agents" description="Identités, rôles, runtimes et politiques de vos agents." actions={<Button icon={Plus} onClick={() => setCreateOpen(true)}>Nouvel agent</Button>} />
      <section className="metric-grid" aria-label="Résumé des agents">
        <article><span className="metric-icon metric-icon--blue"><Bot size={19} /></span><div><small>Agents configurés</small><strong>{agentsQuery.data?.length ?? '—'}</strong><em>{active} actifs</em></div></article>
        <article><span className="metric-icon metric-icon--green"><Boxes size={19} /></span><div><small>Harness utilisés</small><strong>{new Set(agentsQuery.data?.map((agent) => agent.harness).filter(Boolean)).size}</strong><em>ACP local aux workers</em></div></article>
        <article><span className="metric-icon metric-icon--violet"><Workflow size={19} /></span><div><small>Files en cours</small><strong>{agentsQuery.data?.filter((agent) => agent.status === 'busy').length ?? 0}</strong><em>Concurrence contrôlée</em></div></article>
        <article><span className="metric-icon metric-icon--amber"><ShieldCheck size={19} /></span><div><small>Budget par défaut</small><strong>{formatMoney(5)}</strong><em>Par trace</em></div></article>
      </section>
      <section className="data-panel">
        <header className="data-toolbar">
          <div><h2>Répertoire des agents</h2><span>{filtered.length} résultat{filtered.length > 1 ? 's' : ''}</span></div>
          <div className="toolbar-filters">
            <label className="search-field"><Search size={16} /><span className="sr-only">Rechercher un agent</span><input placeholder="Rechercher un agent…" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
            <label className="select-field"><Filter size={15} /><span className="sr-only">Filtrer par statut</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">Tous les statuts</option><option value="active">Actifs</option><option value="busy">Occupés</option><option value="suspended">Suspendus</option><option value="error">En erreur</option></select></label>
          </div>
        </header>
        {agentsQuery.isLoading ? <LoadingState label="Chargement des agents…" /> : null}
        {agentsQuery.error ? <ErrorState error={agentsQuery.error} onRetry={() => void agentsQuery.refetch()} /> : null}
        {!agentsQuery.isLoading && filtered.length === 0 ? <EmptyState title="Aucun agent" description="Créez une identité logique puis choisissez son harness et sa stratégie de runtime." action={<Button icon={Plus} onClick={() => setCreateOpen(true)}>Créer un agent</Button>} /> : null}
        {filtered.length > 0 ? (
          <div className="table-scroll"><table className="data-table"><thead><tr><th>Agent</th><th>Rôle</th><th>Runtime</th><th>Channels</th><th>Concurrence</th><th>Statut</th></tr></thead><tbody>{filtered.map((agent) => <tr key={agent.id}><td><div className="identity-cell"><span className="avatar avatar--agent">{initials(agent.display_name)}</span><span><strong>{agent.display_name}</strong><small>@{agent.handle}</small></span></div></td><td><span className="cell-primary">{agent.role}</span><small>{spacesQuery.data?.find((space) => space.id === agent.space_id)?.name ?? 'Espace'}</small></td><td><span className="runtime-pill"><CircleGauge size={13} /> {agent.harness ?? 'Non assigné'}</span><small>{agent.model ?? 'Modèle automatique'}</small></td><td><strong>{agent.channels.length}</strong></td><td>{agent.max_concurrency} session{agent.max_concurrency > 1 ? 's' : ''}</td><td><StatusBadge status={agent.status} /></td></tr>)}</tbody></table></div>
        ) : null}
      </section>
      <CreateAgentDialog open={createOpen} onOpenChange={setCreateOpen} spaces={spacesQuery.data ?? []} />
    </div>
  )
}
