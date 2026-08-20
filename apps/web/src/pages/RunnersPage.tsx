import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Box, Boxes, Copy, Cpu, HardDrive, KeyRound, Network, Plus, Radio, RefreshCw, Server, TerminalSquare } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { PageHeader } from '../components/PageHeader'
import { Button } from '../components/ui/Button'
import { Dialog } from '../components/ui/Dialog'
import { EmptyState, ErrorState, InlineError, LoadingState } from '../components/ui/Feedback'
import { StatusBadge } from '../components/ui/StatusBadge'
import { api } from '../lib/api'
import { formatRelativeDate } from '../lib/format'

function recordString(record: Record<string, unknown>, key: string, fallback = 'Inconnu'): string {
  return typeof record[key] === 'string' ? record[key] : fallback
}

function RegisterWorkerDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [labels, setLabels] = useState('development, git')
  const mutation = useMutation({
    mutationFn: () => api.workers.register(name, labels.split(',').map((label) => label.trim()).filter(Boolean)),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['workers'] }),
  })
  const setOpen = (value: boolean) => {
    if (!value) { mutation.reset(); setName('') }
    onOpenChange(value)
  }
  const submit = (event: FormEvent) => { event.preventDefault(); mutation.mutate() }
  return <Dialog open={open} onOpenChange={setOpen} title="Enregistrer un worker" description="Le jeton individuel ne sera affiché qu’une seule fois.">{mutation.data ? <div className="worker-secret"><span><KeyRound size={20} /></span><h3>Worker {mutation.data.name} enregistré</h3><p>Copiez ce jeton dans la configuration protégée du LXC, puis fermez cette fenêtre.</p><code>{mutation.data.token}</code><Button icon={Copy} onClick={() => void navigator.clipboard.writeText(mutation.data.token)}>Copier le jeton</Button><small>Empreinte : {mutation.data.token_hint}</small><div className="dialog-actions"><Button variant="secondary" onClick={() => setOpen(false)}>J’ai sauvegardé le jeton</Button></div></div> : <form className="form-stack" onSubmit={submit}><label>Nom du worker<input required autoFocus pattern="[a-zA-Z0-9][a-zA-Z0-9_.-]*" placeholder="worker-dev-01" value={name} onChange={(event) => setName(event.target.value)} /></label><label>Labels séparés par des virgules<input value={labels} onChange={(event) => setLabels(event.target.value)} placeholder="development, git, client-projects" /></label><InlineError error={mutation.error} /><div className="dialog-actions"><Button variant="secondary" onClick={() => setOpen(false)}>Annuler</Button><Button type="submit" isLoading={mutation.isPending}>Générer le jeton</Button></div></form>}</Dialog>
}

export function RunnersPage() {
  const [registerOpen, setRegisterOpen] = useState(false)
  const workersQuery = useQuery({ queryKey: ['workers'], queryFn: api.workers.list, refetchInterval: 15_000 })
  const online = workersQuery.data?.filter((worker) => worker.status === 'online' || worker.status === 'connected').length ?? 0
  const capacity = workersQuery.data?.reduce((sum, worker) => sum + worker.max_sessions, 0) ?? 0
  const active = workersQuery.data?.reduce((sum, worker) => sum + worker.active_sessions, 0) ?? 0
  return <div className="standard-page"><PageHeader eyebrow="Infrastructure LXC" title="Runners" description="Workers connectés, capacités ACP et workspaces déclarés." actions={<><Button variant="secondary" icon={RefreshCw} isLoading={workersQuery.isFetching} onClick={() => void workersQuery.refetch()}>Actualiser</Button><Button icon={Plus} onClick={() => setRegisterOpen(true)}>Enregistrer</Button></>} />
    <section className="metric-grid"><article><span className="metric-icon metric-icon--green"><Radio size={19} /></span><div><small>Workers en ligne</small><strong>{online}/{workersQuery.data?.length ?? 0}</strong><em>Connexions sortantes WSS</em></div></article><article><span className="metric-icon metric-icon--blue"><Cpu size={19} /></span><div><small>Sessions actives</small><strong>{active}</strong><em>{capacity} emplacements au total</em></div></article><article><span className="metric-icon metric-icon--violet"><TerminalSquare size={19} /></span><div><small>Harness détectés</small><strong>{workersQuery.data?.reduce((sum, worker) => sum + worker.harnesses.length, 0) ?? 0}</strong><em>ACP sur stdin/stdout</em></div></article><article><span className="metric-icon metric-icon--amber"><HardDrive size={19} /></span><div><small>Workspaces</small><strong>{workersQuery.data?.reduce((sum, worker) => sum + worker.workspaces.length, 0) ?? 0}</strong><em>Racines pré-enregistrées</em></div></article></section>
    {workersQuery.isLoading ? <LoadingState label="Chargement des workers…" /> : null}{workersQuery.error ? <ErrorState error={workersQuery.error} onRetry={() => void workersQuery.refetch()} /> : null}{!workersQuery.isLoading && workersQuery.data?.length === 0 ? <EmptyState title="Aucun worker enregistré" description="Installez Agent Fleet Worker sur un LXC puis enregistrez son jeton individuel." /> : null}
    <div className="runner-grid">{workersQuery.data?.map((worker) => { const usedPercent = worker.max_sessions > 0 ? Math.round((worker.active_sessions / worker.max_sessions) * 100) : 0; return <article className="runner-card" key={worker.id}><header><span className="runner-icon"><Server size={21} /></span><div><h2>{worker.name}</h2><p>{worker.hostname ?? 'Nom d’hôte non annoncé'}</p></div><StatusBadge status={worker.status} /></header><div className="runner-meta"><span><Network size={14} /> Protocole {worker.protocol_version ?? '—'}</span><span><Box size={14} /> Worker {worker.version ?? '—'}</span><span>Vu {formatRelativeDate(worker.last_heartbeat_at)}</span></div><section><div className="capacity-heading"><span>Capacité sessions</span><strong>{worker.active_sessions} / {worker.max_sessions}</strong></div><div className="progress"><span style={{ width: `${usedPercent}%` }} /></div><small>{worker.available_sessions} emplacements disponibles</small></section><div className="runner-columns"><section><h3><TerminalSquare size={15} /> Harness</h3>{worker.harnesses.length === 0 ? <p className="muted">Aucun inventaire</p> : worker.harnesses.map((harness, index) => <div className="inventory-row" key={`${recordString(harness, 'type')}-${index}`}><span><strong>{recordString(harness, 'type')}</strong><small>{recordString(harness, 'adapter', 'Adaptateur ACP')}</small></span><em>{recordString(harness, 'version', '—')}</em></div>)}</section><section><h3><Boxes size={15} /> Workspaces</h3>{worker.workspaces.length === 0 ? <p className="muted">Aucun workspace</p> : worker.workspaces.map((workspace, index) => <div className="inventory-row" key={`${recordString(workspace, 'id')}-${index}`}><span><strong>{recordString(workspace, 'display_name')}</strong><small>{recordString(workspace, 'root')}</small></span></div>)}</section></div><footer><span className="tag-list">{worker.labels.map((label) => <em key={label}>{label}</em>)}</span></footer></article> })}</div>
    <RegisterWorkerDialog open={registerOpen} onOpenChange={setRegisterOpen} />
  </div>
}
