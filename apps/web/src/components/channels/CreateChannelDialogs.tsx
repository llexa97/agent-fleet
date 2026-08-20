import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../../lib/api'
import { slugify } from '../../lib/format'
import type { Space, UUID } from '../../types/api'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'
import { InlineError } from '../ui/Feedback'

export function CreateSpaceDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [kind, setKind] = useState<'business' | 'personal' | 'custom'>('business')
  const mutation = useMutation({
    mutationFn: () => api.spaces.create({ name, slug, kind }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['spaces'] })
      setName('')
      setSlug('')
      onOpenChange(false)
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    mutation.mutate()
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Créer un espace" description="Les données et permissions restent isolées entre espaces.">
      <form className="form-stack" onSubmit={submit}>
        <label>Nom<input required autoFocus value={name} onChange={(event) => { setName(event.target.value); setSlug(slugify(event.target.value)) }} /></label>
        <label>Identifiant<input required pattern="[a-z0-9][a-z0-9-]*" value={slug} onChange={(event) => setSlug(slugify(event.target.value))} /></label>
        <label>Type<select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="business">Business</option><option value="personal">Personnel</option><option value="custom">Personnalisé</option></select></label>
        <InlineError error={mutation.error} />
        <div className="dialog-actions"><Button variant="secondary" onClick={() => onOpenChange(false)}>Annuler</Button><Button type="submit" isLoading={mutation.isPending}>Créer l’espace</Button></div>
      </form>
    </Dialog>
  )
}

export function CreateChannelDialog({
  open,
  onOpenChange,
  spaces,
  initialSpaceId,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  spaces: Space[]
  initialSpaceId?: UUID
  onCreated: (channelId: UUID, spaceId: UUID) => void
}) {
  const queryClient = useQueryClient()
  const [spaceId, setSpaceId] = useState(initialSpaceId ?? '')
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [kind, setKind] = useState('discussion')
  const effectiveSpaceId = spaceId || initialSpaceId || ''
  const mutation = useMutation({
    mutationFn: () => api.channels.create({ space_id: effectiveSpaceId, name, slug, kind }),
    onSuccess: async (channel) => {
      await queryClient.invalidateQueries({ queryKey: ['channels', channel.space_id] })
      setName('')
      setSlug('')
      onOpenChange(false)
      onCreated(channel.id, channel.space_id)
    },
  })
  const submit = (event: FormEvent) => { event.preventDefault(); mutation.mutate() }
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Créer un channel" description="Un channel coordonne messages, agents, tâches et traces.">
      <form className="form-stack" onSubmit={submit}>
        <label>Espace<select required value={effectiveSpaceId} onChange={(event) => setSpaceId(event.target.value)}><option value="" disabled>Choisir…</option>{spaces.map((space) => <option key={space.id} value={space.id}>{space.name}</option>)}</select></label>
        <label>Nom<input required autoFocus value={name} onChange={(event) => { setName(event.target.value); setSlug(slugify(event.target.value)) }} /></label>
        <label>Identifiant<input required pattern="[a-z0-9][a-z0-9-]*" value={slug} onChange={(event) => setSlug(slugify(event.target.value))} /></label>
        <label>Type<select value={kind} onChange={(event) => setKind(event.target.value)}><option value="discussion">Discussion</option><option value="project">Projet</option><option value="private">Privé</option></select></label>
        <InlineError error={mutation.error} />
        <div className="dialog-actions"><Button variant="secondary" onClick={() => onOpenChange(false)}>Annuler</Button><Button type="submit" isLoading={mutation.isPending}>Créer le channel</Button></div>
      </form>
    </Dialog>
  )
}
