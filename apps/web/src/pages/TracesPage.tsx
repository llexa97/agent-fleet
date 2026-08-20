import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, CircleStop, Clock3, Coins, GitBranch, Pause, Play, RotateCcw, Search, XCircle } from 'lucide-react'
import { useMemo, useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { Button } from '../components/ui/Button'
import { EmptyState, ErrorState, InlineError, LoadingState } from '../components/ui/Feedback'
import { StatusBadge } from '../components/ui/StatusBadge'
import { api } from '../lib/api'
import { formatCompactNumber, formatMoney, formatRelativeDate } from '../lib/format'
import type { Trace } from '../types/api'

function TraceTree({ traces, selected, onSelect }: { traces: Trace[]; selected: string | null; onSelect: (id: string) => void }) {
  const children = useMemo(() => {
    const result = new Map<string | null, Trace[]>()
    for (const trace of traces) {
      const parent = trace.parent_trace_id && traces.some((item) => item.id === trace.parent_trace_id) ? trace.parent_trace_id : null
      result.set(parent, [...(result.get(parent) ?? []), trace])
    }
    return result
  }, [traces])
  const renderLevel = (parent: string | null, depth: number, visited: Set<string>): React.ReactNode => (children.get(parent) ?? []).map((trace) => {
    if (visited.has(trace.id)) return null
    const nextVisited = new Set(visited).add(trace.id)
    return <li key={trace.id}><button className={selected === trace.id ? 'is-active' : ''} style={{ '--tree-depth': depth } as React.CSSProperties} onClick={() => onSelect(trace.id)}><span className="trace-node"><Bot size={14} /></span><span><strong>TRACE-{trace.id.slice(0, 8).toUpperCase()}</strong><small>{trace.turn_count} tours · {formatRelativeDate(trace.created_at)}</small></span><StatusBadge status={trace.status} /></button>{(children.get(trace.id)?.length ?? 0) > 0 ? <ul>{renderLevel(trace.id, depth + 1, nextVisited)}</ul> : null}</li>
  })
  return <ul className="trace-tree">{renderLevel(null, 0, new Set())}</ul>
}

export function TracesPage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const tracesQuery = useQuery({ queryKey: ['traces'], queryFn: api.traces.list })
  const [selectedId, setSelectedId] = useState<string | null>(new URLSearchParams(window.location.search).get('selected'))
  const filtered = (tracesQuery.data ?? []).filter((trace) => !search || trace.id.toLowerCase().includes(search.toLowerCase()) || trace.status.includes(search.toLowerCase()))
  const selected = tracesQuery.data?.find((trace) => trace.id === selectedId) ?? filtered[0] ?? null
  const action = useMutation({
    mutationFn: ({ id, type }: { id: string; type: 'pause' | 'resume' | 'cancel' }) => api.traces.action(id, type),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['traces'] }),
  })
  return <div className="standard-page"><PageHeader eyebrow="Orchestration" title="Traces" description="Visualisez chaque chaîne d’agents, ses limites et sa consommation." />
    {tracesQuery.isLoading ? <LoadingState label="Chargement des traces…" /> : null}
    {tracesQuery.error ? <ErrorState error={tracesQuery.error} onRetry={() => void tracesQuery.refetch()} /> : null}
    {!tracesQuery.isLoading && tracesQuery.data?.length === 0 ? <EmptyState title="Aucune trace" description="Une trace apparaîtra dès qu’une mention déclenchera un agent." /> : null}
    {(tracesQuery.data?.length ?? 0) > 0 ? <div className="trace-layout"><section className="trace-browser"><header><div><h2>Arbre d’orchestration</h2><span>{filtered.length} traces</span></div><label className="search-field search-field--compact"><Search size={15} /><span className="sr-only">Rechercher</span><input placeholder="ID ou statut…" value={search} onChange={(event) => setSearch(event.target.value)} /></label></header><TraceTree traces={filtered} selected={selected?.id ?? null} onSelect={setSelectedId} /></section>
      <section className="trace-detail">{selected ? <><header><div><span className="eyebrow">Trace active</span><h2>TRACE-{selected.id.slice(0, 8).toUpperCase()}</h2><StatusBadge status={selected.status} /></div><div className="trace-actions">{selected.status === 'paused' ? <Button size="small" icon={Play} onClick={() => action.mutate({ id: selected.id, type: 'resume' })}>Continuer</Button> : <Button variant="secondary" size="small" icon={Pause} onClick={() => action.mutate({ id: selected.id, type: 'pause' })}>Pause</Button>}<Button variant="danger" size="small" icon={CircleStop} onClick={() => action.mutate({ id: selected.id, type: 'cancel' })}>Annuler</Button></div></header><InlineError error={action.error} /><div className="trace-metrics"><article><GitBranch size={17} /><span><small>Tours</small><strong>{selected.turn_count}</strong></span></article><article><Bot size={17} /><span><small>Délégations</small><strong>{selected.delegation_count}</strong></span></article><article><Coins size={17} /><span><small>Coût</small><strong>{formatMoney(selected.cost_eur)}</strong></span></article><article><Clock3 size={17} /><span><small>Tokens</small><strong>{formatCompactNumber(selected.token_count)}</strong></span></article></div><div className="trace-section"><h3>Contrôles d’orchestration</h3><dl className="detail-list"><div><dt>Profondeur maximale</dt><dd>{selected.max_depth_seen}</dd></div><div><dt>Agents parallèles (pic)</dt><dd>{selected.parallel_agents_peak}</dd></div><div><dt>Début</dt><dd>{formatRelativeDate(selected.created_at)}</dd></div><div><dt>Cause d’arrêt</dt><dd>{selected.stop_reason ?? 'Aucune'}</dd></div></dl></div><div className="trace-section"><h3>Politiques appliquées</h3><pre className="json-preview">{JSON.stringify(selected.policy, null, 2)}</pre></div>{selected.status === 'failed' || selected.status === 'cancelled' ? <div className="stop-banner"><XCircle size={18} /><span><strong>Trace interrompue</strong><small>{selected.stop_reason ?? 'Interruption demandée par le Control Plane.'}</small></span><Button variant="secondary" size="small" icon={RotateCcw}>Réassigner</Button></div> : null}</> : null}</section>
    </div> : null}
  </div>
}
