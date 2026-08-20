import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AtSign,
  Bot,
  BriefcaseBusiness,
  ChevronDown,
  CircleUserRound,
  Hash,
  LockKeyhole,
  MessageSquareText,
  MoreHorizontal,
  Plus,
  Search,
  UserRound,
  UsersRound,
  Wrench,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { CreateChannelDialog, CreateSpaceDialog } from '../components/channels/CreateChannelDialogs'
import { ChannelAgentsDialog } from '../components/channels/ChannelAgentsDialog'
import { MentionComposer } from '../components/channels/MentionComposer'
import { MessageItem } from '../components/channels/MessageItem'
import { Button } from '../components/ui/Button'
import { EmptyState, ErrorState, InlineError, LoadingState } from '../components/ui/Feedback'
import { StatusBadge } from '../components/ui/StatusBadge'
import { api } from '../lib/api'
import { initials } from '../lib/format'
import { useRealtime } from '../realtime/RealtimeProvider'
import type { Message, UUID } from '../types/api'

export function ChannelsPage() {
  const { channelId: routeChannelId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const messageEnd = useRef<HTMLDivElement>(null)
  const { events } = useRealtime()
  const [spaceId, setSpaceId] = useState<UUID | undefined>()
  const [spaceDialogOpen, setSpaceDialogOpen] = useState(false)
  const [channelDialogOpen, setChannelDialogOpen] = useState(false)
  const [agentsDialogOpen, setAgentsDialogOpen] = useState(false)
  const [replyTo, setReplyTo] = useState<Message | null>(null)
  const [channelSearch, setChannelSearch] = useState('')

  const spacesQuery = useQuery({ queryKey: ['spaces'], queryFn: api.spaces.list })
  const effectiveSpaceId = spaceId ?? spacesQuery.data?.[0]?.id

  const channelsQuery = useQuery({
    queryKey: ['channels', effectiveSpaceId],
    queryFn: () => api.channels.list(effectiveSpaceId),
    enabled: Boolean(effectiveSpaceId),
  })
  const selectedChannel = channelsQuery.data?.find((channel) => channel.id === routeChannelId)
  useEffect(() => {
    if (!routeChannelId && channelsQuery.data?.[0]) void navigate(`/channels/${channelsQuery.data[0].id}`, { replace: true })
  }, [channelsQuery.data, navigate, routeChannelId])

  const messagesQuery = useQuery({
    queryKey: ['messages', routeChannelId],
    queryFn: () => api.channels.messages(routeChannelId ?? ''),
    enabled: Boolean(routeChannelId),
    refetchInterval: 45_000,
  })
  const membersQuery = useQuery({
    queryKey: ['channel-members', routeChannelId],
    queryFn: () => api.channels.members(routeChannelId ?? ''),
    enabled: Boolean(routeChannelId),
  })
  const permissionsQuery = useQuery({
    queryKey: ['permissions'],
    queryFn: () => api.permissions.list(),
  })

  useEffect(() => {
    messageEnd.current?.scrollIntoView({ block: 'end' })
  }, [messagesQuery.data?.length, routeChannelId])

  const postMutation = useMutation({
    mutationFn: (input: Parameters<typeof api.channels.postMessage>[1]) =>
      api.channels.postMessage(routeChannelId ?? '', input),
    onSuccess: (message) => {
      queryClient.setQueryData<Message[]>(['messages', routeChannelId], (current = []) =>
        current.some((item) => item.id === message.id) ? current : [...current, message],
      )
      setReplyTo(null)
    },
    onSettled: async () => queryClient.invalidateQueries({ queryKey: ['messages', routeChannelId] }),
  })

  const filteredChannels = useMemo(() => {
    const search = channelSearch.trim().toLowerCase()
    return (channelsQuery.data ?? []).filter((channel) =>
      !search || channel.name.toLowerCase().includes(search) || channel.slug.includes(search),
    )
  }, [channelSearch, channelsQuery.data])

  const latestExecutionEvent = events.find((event) =>
    event.channel_id === routeChannelId && /^(session|agent|delivery)\./.test(event.event_type),
  )
  const recentActivity = latestExecutionEvent &&
    ['session.updated', 'agent.started', 'delivery.claimed', 'delivery.processing'].includes(latestExecutionEvent.event_type)
    ? latestExecutionEvent
    : null
  const activeMemberCount = membersQuery.data?.filter((member) => member.actor_type === 'agent').length ?? 0
  const pendingPermissions = permissionsQuery.data?.filter((permission) => permission.status === 'pending') ?? []

  if (spacesQuery.isLoading) return <LoadingState label="Chargement de vos espaces…" />
  if (spacesQuery.error) return <ErrorState error={spacesQuery.error} onRetry={() => void spacesQuery.refetch()} />

  return (
    <div className="channels-layout">
      <aside className="channel-sidebar" aria-label="Espaces et channels">
        <div className="channel-sidebar__heading">
          <span className="eyebrow">Espace</span>
          <button type="button" className="space-selector" onClick={() => setSpaceDialogOpen(true)}>
            <span className="space-icon"><BriefcaseBusiness size={17} /></span>
            <span>{spacesQuery.data?.find((space) => space.id === effectiveSpaceId)?.name ?? 'Créer un espace'}</span>
            <ChevronDown size={15} />
          </button>
        </div>
        {(spacesQuery.data?.length ?? 0) > 1 ? (
          <div className="space-tabs" role="list" aria-label="Choisir un espace">
            {spacesQuery.data?.map((space) => (
              <button key={space.id} className={space.id === effectiveSpaceId ? 'is-active' : ''} onClick={() => { setSpaceId(space.id); void navigate('/channels') }}>
                {space.name}<small>{space.kind}</small>
              </button>
            ))}
          </div>
        ) : null}
        <div className="channel-list-heading">
          <span>Channels</span>
          <button type="button" aria-label="Créer un channel" onClick={() => setChannelDialogOpen(true)}><Plus size={16} /></button>
        </div>
        <label className="search-field search-field--compact">
          <Search size={15} aria-hidden="true" />
          <span className="sr-only">Rechercher un channel</span>
          <input value={channelSearch} onChange={(event) => setChannelSearch(event.target.value)} placeholder="Rechercher…" />
        </label>
        <nav className="channel-list" aria-label="Channels">
          {channelsQuery.isLoading ? <LoadingState label="Channels…" /> : null}
          {filteredChannels.map((channel) => (
            <button
              key={channel.id}
              type="button"
              className={channel.id === routeChannelId ? 'is-active' : ''}
              onClick={() => void navigate(`/channels/${channel.id}`)}
            >
              {channel.kind === 'private' ? <LockKeyhole size={15} /> : <Hash size={16} />}
              <span>{channel.name}</span>
              {channel.kind === 'project' ? <em>Projet</em> : null}
            </button>
          ))}
        </nav>
        {!channelsQuery.isLoading && filteredChannels.length === 0 ? (
          <div className="channel-list-empty"><MessageSquareText size={20} /><span>Aucun channel</span><button onClick={() => setChannelDialogOpen(true)}>Créer le premier</button></div>
        ) : null}
        <div className="channel-sidebar__agents">
          <span>Agents dans le channel</span>
          <div className="avatar-stack">
            {(membersQuery.data ?? []).filter((member) => member.actor_type === 'agent').slice(0, 5).map((member) => (
              <span key={member.actor_id} className="avatar avatar--agent" title={member.handle ? `@${member.handle}` : member.display_name}><Bot size={14} /></span>
            ))}
            {activeMemberCount === 0 ? <small>Aucun agent</small> : null}
          </div>
        </div>
      </aside>

      <section className="conversation" aria-label={selectedChannel ? `Channel ${selectedChannel.name}` : 'Conversation'}>
        {selectedChannel ? (
          <>
            <header className="conversation__header">
              <div className="conversation__title"><span><Hash size={20} /></span><div><h1>{selectedChannel.name}</h1><p>{selectedChannel.description ?? `${membersQuery.data?.length ?? 0} membres · ${activeMemberCount} agents`}</p></div></div>
              <div className="mobile-channel-controls">
                <select aria-label="Choisir un espace" value={effectiveSpaceId} onChange={(event) => { setSpaceId(event.target.value); void navigate('/channels') }}>{spacesQuery.data?.map((space) => <option key={space.id} value={space.id}>{space.name}</option>)}</select>
                <select aria-label="Choisir un channel" value={routeChannelId} onChange={(event) => void navigate(`/channels/${event.target.value}`)}>{channelsQuery.data?.map((channel) => <option key={channel.id} value={channel.id}>#{channel.name}</option>)}</select>
                <button type="button" aria-label="Créer un channel" onClick={() => setChannelDialogOpen(true)}><Plus size={16} /></button>
              </div>
              <div className="conversation__actions"><div className="avatar-stack avatar-stack--header">{(membersQuery.data ?? []).slice(0, 4).map((member) => <span key={member.actor_id} className={`avatar avatar--${member.actor_type}`}>{member.actor_type === 'agent' ? <Bot size={13} /> : initials(member.display_name)}</span>)}</div><button type="button" className="icon-button" aria-label="Gérer les agents du channel" onClick={() => setAgentsDialogOpen(true)}><MoreHorizontal size={19} /></button></div>
            </header>
            <div className="message-list" aria-live="polite" aria-busy={messagesQuery.isLoading}>
              {messagesQuery.isLoading ? <LoadingState label="Chargement de l’historique…" /> : null}
              {messagesQuery.error ? <ErrorState error={messagesQuery.error} onRetry={() => void messagesQuery.refetch()} /> : null}
              {!messagesQuery.isLoading && messagesQuery.data?.length === 0 ? (
                <EmptyState title={`Bienvenue dans #${selectedChannel.name}`} description="Publiez le premier message ou mentionnez un agent avec @." />
              ) : null}
              {messagesQuery.data?.map((message) => <MessageItem key={message.id} message={message} onReply={setReplyTo} />)}
              {recentActivity ? (
                <div className="agent-working" role="status"><span className="avatar avatar--agent"><Bot size={15} /></span><span><strong>Un agent travaille</strong><small>Les mises à jour ACP arrivent en temps réel</small></span><i /><i /><i /></div>
              ) : null}
              <div ref={messageEnd} />
            </div>
            <div className="conversation__composer">
              <InlineError error={postMutation.error} />
              <MentionComposer
                members={membersQuery.data ?? []}
                isSending={postMutation.isPending}
                replyTo={replyTo ? { id: replyTo.id, author: replyTo.author_display_name ?? replyTo.author_handle ?? 'ce message' } : null}
                onCancelReply={() => setReplyTo(null)}
                onSend={async (input) => {
                  await postMutation.mutateAsync({ ...input, ...(replyTo ? { reply_to_id: replyTo.id } : {}) })
                }}
              />
            </div>
          </>
        ) : (
          <EmptyState title="Sélectionnez un channel" description="Choisissez une conversation dans la colonne de gauche ou créez un nouveau channel." action={<Button icon={Plus} onClick={() => setChannelDialogOpen(true)}>Nouveau channel</Button>} />
        )}
      </section>

      <aside className="context-panel" aria-label="Contexte du channel">
        <header><div><span className="eyebrow">Contexte</span><h2>Vue d’ensemble</h2></div><Wrench size={18} /></header>
        <section className="context-card">
          <div className="context-card__title"><UsersRound size={16} /><h3>Participants</h3><span>{membersQuery.data?.length ?? 0}</span></div>
          <div className="member-list">
            {(membersQuery.data ?? []).map((member) => (
              <div key={member.actor_id} className="member-row">
                <span className={`avatar avatar--${member.actor_type}`}>{member.actor_type === 'agent' ? <Bot size={14} /> : <UserRound size={14} />}</span>
                <span><strong>{member.handle ? `@${member.handle}` : member.display_name}</strong><small>{member.role}</small></span>
                {member.actor_type === 'agent' ? <span className="presence-dot" title="Agent configuré" /> : null}
              </div>
            ))}
            {!routeChannelId ? <p className="muted">Aucun channel sélectionné.</p> : null}
          </div>
        </section>
        <section className="context-card">
          <div className="context-card__title"><AtSign size={16} /><h3>Activité</h3></div>
          <dl className="mini-stats"><div><dt>Messages</dt><dd>{messagesQuery.data?.length ?? '—'}</dd></div><div><dt>Agents</dt><dd>{activeMemberCount}</dd></div></dl>
          <a className="context-link" href="/traces">Voir les traces actives</a>
        </section>
        <section className={`context-card ${pendingPermissions.length > 0 ? 'context-card--alert' : ''}`}>
          <div className="context-card__title"><CircleUserRound size={16} /><h3>Approbations</h3>{pendingPermissions.length > 0 ? <span>{pendingPermissions.length}</span> : null}</div>
          {pendingPermissions.length === 0 ? <p className="muted">Aucune permission en attente.</p> : pendingPermissions.slice(0, 2).map((permission) => <div className="permission-preview" key={permission.id}><StatusBadge status={permission.status} /><strong>{permission.action_summary}</strong><a href="/settings">Examiner</a></div>)}
        </section>
      </aside>

      <CreateSpaceDialog open={spaceDialogOpen} onOpenChange={setSpaceDialogOpen} />
      <CreateChannelDialog
        open={channelDialogOpen}
        onOpenChange={setChannelDialogOpen}
        spaces={spacesQuery.data ?? []}
        initialSpaceId={effectiveSpaceId}
        onCreated={(newChannelId, newSpaceId) => { setSpaceId(newSpaceId); void navigate(`/channels/${newChannelId}`) }}
      />
      {selectedChannel ? (
        <ChannelAgentsDialog
          open={agentsDialogOpen}
          onOpenChange={setAgentsDialogOpen}
          channelId={selectedChannel.id}
          channelName={selectedChannel.name}
          spaceId={selectedChannel.space_id}
          members={membersQuery.data ?? []}
        />
      ) : null}
    </div>
  )
}
