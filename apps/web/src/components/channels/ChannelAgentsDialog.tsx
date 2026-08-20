import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { api } from '../../lib/api'
import type { ChannelMember, UUID } from '../../types/api'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { InlineError, LoadingState } from '../ui/Feedback'

interface ChannelAgentsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  channelId: UUID
  channelName: string
  spaceId: UUID
  members: ChannelMember[]
}

export function ChannelAgentsDialog({
  open,
  onOpenChange,
  channelId,
  channelName,
  spaceId,
  members,
}: ChannelAgentsDialogProps) {
  const queryClient = useQueryClient()
  const [agentId, setAgentId] = useState('')
  const agentsQuery = useQuery({
    queryKey: ['agents', spaceId],
    queryFn: () => api.agents.list(spaceId),
    enabled: open,
  })
  const currentAgentIds = useMemo(
    () => new Set(members.flatMap((member) => member.agent_id ? [member.agent_id] : [])),
    [members],
  )
  const availableAgents = (agentsQuery.data ?? []).filter(
    (agent) => !currentAgentIds.has(agent.id),
  )
  const currentAgents = members.filter((member) => member.actor_type === 'agent')
  const mutation = useMutation({
    mutationFn: () => api.agents.addMembership(agentId, channelId),
    onSuccess: async () => {
      setAgentId('')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['channel-members', channelId] }),
        queryClient.invalidateQueries({ queryKey: ['agents'] }),
      ])
    },
  })

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Gérer les agents de #${channelName}`}
      description="Ajoutez un agent logique de cet espace. Il sera activé uniquement lorsqu’il est mentionné ou assigné."
    >
      <div className="channel-agents-dialog">
        <section>
          <h3>Agents présents</h3>
          {currentAgents.length === 0 ? <p className="muted">Aucun agent dans ce channel.</p> : (
            <div className="channel-agent-list">
              {currentAgents.map((member) => (
                <div key={member.actor_id}>
                  <span className="avatar avatar--agent"><Bot size={14} /></span>
                  <span><strong>@{member.handle}</strong><small>{member.display_name} · {member.role}</small></span>
                </div>
              ))}
            </div>
          )}
        </section>
        <section className="form-stack">
          <h3>Ajouter un agent</h3>
          {agentsQuery.isLoading ? <LoadingState label="Chargement des agents…" /> : null}
          {!agentsQuery.isLoading && (agentsQuery.data?.length ?? 0) === 0 ? (
            <p className="muted">Aucun agent n’existe dans cet espace. <a href="/agents">Créer un agent</a></p>
          ) : null}
          {!agentsQuery.isLoading && availableAgents.length === 0 && (agentsQuery.data?.length ?? 0) > 0 ? (
            <p className="muted">Tous les agents de cet espace appartiennent déjà au channel.</p>
          ) : null}
          {availableAgents.length > 0 ? (
            <div className="channel-agent-add">
              <label>
                Agent à ajouter
                <select value={agentId} onChange={(event) => setAgentId(event.target.value)}>
                  <option value="">Choisir un agent…</option>
                  {availableAgents.map((agent) => (
                    <option key={agent.id} value={agent.id}>@{agent.handle} · {agent.display_name}</option>
                  ))}
                </select>
              </label>
              <Button
                icon={Plus}
                disabled={!agentId}
                isLoading={mutation.isPending}
                onClick={() => mutation.mutate()}
              >
                Ajouter
              </Button>
            </div>
          ) : null}
          <InlineError error={agentsQuery.error ?? mutation.error} />
        </section>
        <div className="dialog-actions">
          <Button variant="secondary" onClick={() => onOpenChange(false)}>Fermer</Button>
        </div>
      </div>
    </Dialog>
  )
}
