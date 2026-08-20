import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, CalendarClock, CircleDot, GitBranch, Plus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { PageHeader } from '../components/PageHeader'
import { Button } from '../components/ui/Button'
import { Dialog } from '../components/ui/Dialog'
import { EmptyState, ErrorState, InlineError, LoadingState } from '../components/ui/Feedback'
import { api } from '../lib/api'
import { formatRelativeDate } from '../lib/format'
import type { Agent, Channel, Space } from '../types/api'

const columns = [
  { key: 'backlog', label: 'Backlog', statuses: ['backlog'] },
  { key: 'queued', label: 'Queued', statuses: ['queued'] },
  { key: 'running', label: 'Running', statuses: ['running'] },
  { key: 'waiting', label: 'Waiting', statuses: ['waiting_input', 'waiting_approval', 'blocked'] },
  { key: 'review', label: 'Review', statuses: ['review'] },
  { key: 'completed', label: 'Completed', statuses: ['completed'] },
  { key: 'failed', label: 'Failed', statuses: ['failed', 'cancelled'] },
] as const

function CreateTaskDialog({ open, onOpenChange, spaces, agents, channels }: { open: boolean; onOpenChange: (open: boolean) => void; spaces: Space[]; agents: Agent[]; channels: Channel[] }) {
  const queryClient = useQueryClient()
  const [spaceId, setSpaceId] = useState('')
  const [channelId, setChannelId] = useState('')
  const [agentId, setAgentId] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState(2)
  const mutation = useMutation({
    mutationFn: () => api.tasks.create({ space_id: spaceId, ...(channelId ? { channel_id: channelId } : {}), ...(agentId ? { assigned_agent_id: agentId } : {}), title, description, priority }),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['tasks'] }); onOpenChange(false); setTitle(''); setDescription('') },
  })
  const submit = (event: FormEvent) => { event.preventDefault(); mutation.mutate() }
  return <Dialog open={open} onOpenChange={onOpenChange} title="Créer une tâche" description="La tâche est persistante et peut être suivie dans une trace."><form className="form-stack" onSubmit={submit}><label>Espace<select required value={spaceId} onChange={(event) => { setSpaceId(event.target.value); setChannelId(''); setAgentId('') }}><option value="" disabled>Choisir…</option>{spaces.map((space) => <option key={space.id} value={space.id}>{space.name}</option>)}</select></label><label>Titre<input required autoFocus value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>Description<textarea rows={4} value={description} onChange={(event) => setDescription(event.target.value)} /></label><div className="form-grid"><label>Channel<select value={channelId} onChange={(event) => setChannelId(event.target.value)}><option value="">Aucun</option>{channels.filter((channel) => channel.space_id === spaceId).map((channel) => <option key={channel.id} value={channel.id}>#{channel.name}</option>)}</select></label><label>Agent assigné<select value={agentId} onChange={(event) => setAgentId(event.target.value)}><option value="">Non assignée</option>{agents.filter((agent) => agent.space_id === spaceId).map((agent) => <option key={agent.id} value={agent.id}>@{agent.handle}</option>)}</select></label></div><label>Priorité<select value={priority} onChange={(event) => setPriority(Number(event.target.value))}><option value={0}>Très basse</option><option value={1}>Basse</option><option value={2}>Normale</option><option value={3}>Haute</option><option value={4}>Critique</option></select></label><InlineError error={mutation.error} /><div className="dialog-actions"><Button variant="secondary" onClick={() => onOpenChange(false)}>Annuler</Button><Button type="submit" isLoading={mutation.isPending}>Créer la tâche</Button></div></form></Dialog>
}

export function TasksPage() {
  const [createOpen, setCreateOpen] = useState(false)
  const tasksQuery = useQuery({ queryKey: ['tasks'], queryFn: api.tasks.list })
  const agentsQuery = useQuery({ queryKey: ['agents'], queryFn: () => api.agents.list() })
  const spacesQuery = useQuery({ queryKey: ['spaces'], queryFn: api.spaces.list })
  const channelsQuery = useQuery({ queryKey: ['channels', 'all'], queryFn: () => api.channels.list() })
  if (tasksQuery.isLoading) return <div className="standard-page"><PageHeader title="Tasks" /><LoadingState label="Chargement des tâches…" /></div>
  if (tasksQuery.error) return <ErrorState error={tasksQuery.error} onRetry={() => void tasksQuery.refetch()} />
  return <div className="standard-page standard-page--wide"><PageHeader eyebrow="Exécution persistante" title="Tasks" description="Suivez les délégations, dépendances et livrables de la flotte." actions={<Button icon={Plus} onClick={() => setCreateOpen(true)}>Nouvelle tâche</Button>} />
    {(tasksQuery.data?.length ?? 0) === 0 ? <EmptyState title="Aucune tâche" description="Créez une tâche durable ou déléguez depuis un channel." action={<Button icon={Plus} onClick={() => setCreateOpen(true)}>Créer une tâche</Button>} /> : <div className="kanban" aria-label="Tableau des tâches">{columns.map((column) => { const tasks = tasksQuery.data?.filter((task) => column.statuses.includes(task.status as never)) ?? []; return <section className={`kanban-column kanban-column--${column.key}`} key={column.key}><header><span className="kanban-dot" /><h2>{column.label}</h2><em>{tasks.length}</em></header><div className="kanban-cards">{tasks.map((task) => { const agent = agentsQuery.data?.find((item) => item.id === task.assigned_agent_id); const channel = channelsQuery.data?.find((item) => item.id === task.channel_id); return <article className="task-card" key={task.id}><div className="task-card__meta"><span className={`priority priority--${task.priority}`}>P{4 - task.priority}</span>{task.parent_task_id ? <span><CircleDot size={12} /> Sous-tâche</span> : null}</div><h3>{task.title}</h3>{task.description ? <p>{task.description}</p> : null}<div className="task-card__links">{channel ? <span># {channel.name}</span> : null}{task.trace_id ? <span><GitBranch size={12} /> Trace</span> : null}</div><footer>{agent ? <span className="task-assignee"><Bot size={13} /> @{agent.handle}</span> : <span className="muted">Non assignée</span>}<time title={task.deadline ?? task.created_at}><CalendarClock size={13} /> {task.deadline ? formatRelativeDate(task.deadline) : formatRelativeDate(task.created_at)}</time></footer></article>})}{tasks.length === 0 ? <p className="kanban-empty">Aucune tâche</p> : null}</div></section> })}</div>}
    <CreateTaskDialog open={createOpen} onOpenChange={setCreateOpen} spaces={spacesQuery.data ?? []} agents={agentsQuery.data ?? []} channels={channelsQuery.data ?? []} />
  </div>
}
