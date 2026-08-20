import { AtSign, CornerDownLeft, Send, X } from 'lucide-react'
import { useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import type { ChannelMember, MessageMentionInput } from '../../types/api'
import { Button } from '../ui/Button'

interface MentionComposerProps {
  members: ChannelMember[]
  isSending: boolean
  disabled?: boolean
  replyTo?: { id: string; author: string } | null
  onCancelReply?: () => void
  onSend: (input: {
    content: string
    mentions: MessageMentionInput[]
    expects_response: boolean
  }) => Promise<void>
}

interface MentionQuery {
  start: number
  end: number
  value: string
}

function mentionQueryAt(text: string, cursor: number): MentionQuery | null {
  const before = text.slice(0, cursor)
  const match = /(?:^|\s)@([a-z0-9-]*)$/i.exec(before)
  if (!match) return null
  const value = match[1] ?? ''
  const at = before.lastIndexOf('@')
  return { start: at, end: cursor, value }
}

function includesHandle(content: string, handle: string): boolean {
  const escaped = handle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`(^|\\s)@${escaped}(?=\\s|[.,!?;:]|$)`, 'i').test(content)
}

export function MentionComposer({
  members,
  isSending,
  disabled = false,
  replyTo,
  onCancelReply,
  onSend,
}: MentionComposerProps) {
  const textarea = useRef<HTMLTextAreaElement>(null)
  const [content, setContent] = useState('')
  const [query, setQuery] = useState<MentionQuery | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const [selected, setSelected] = useState<Map<string, ChannelMember>>(new Map())

  const candidates = useMemo(() => {
    if (!query) return []
    const normalized = query.value.toLowerCase()
    return members
      .filter((member) => member.handle)
      .filter((member) => {
        const handle = member.handle?.toLowerCase() ?? ''
        return handle.includes(normalized) || member.display_name.toLowerCase().includes(normalized)
      })
      .sort((left, right) => {
        if (left.actor_type !== right.actor_type) return left.actor_type === 'agent' ? -1 : 1
        return (left.handle ?? '').localeCompare(right.handle ?? '')
      })
      .slice(0, 8)
  }, [members, query])

  const updateQuery = (nextContent: string, cursor: number) => {
    const next = mentionQueryAt(nextContent, cursor)
    setQuery(next)
    setActiveIndex(0)
  }

  const selectMember = (member: ChannelMember) => {
    if (!query || !member.handle) return
    const next = `${content.slice(0, query.start)}@${member.handle} ${content.slice(query.end)}`
    const cursor = query.start + member.handle.length + 2
    setContent(next)
    setSelected((current) => new Map(current).set(member.handle ?? '', member))
    setQuery(null)
    window.requestAnimationFrame(() => {
      textarea.current?.focus()
      textarea.current?.setSelectionRange(cursor, cursor)
    })
  }

  const submit = async (event?: FormEvent) => {
    event?.preventDefault()
    const trimmed = content.trim()
    if (!trimmed || isSending || disabled) return
    const structured = [...selected.entries()]
      .filter(([handle]) => includesHandle(trimmed, handle))
      .map(([handle, member]): MessageMentionInput => ({
        target_type: member.actor_type === 'agent' ? 'agent' : 'human',
        target_id: member.actor_type === 'agent' && member.agent_id ? member.agent_id : member.actor_id,
        handle_at_creation: handle,
      }))
    await onSend({
      content: trimmed,
      mentions: structured,
      expects_response: structured.some((mention) => mention.target_type === 'agent'),
    })
    setContent('')
    setSelected(new Map())
    setQuery(null)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (query && candidates.length > 0) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActiveIndex((index) => (index + 1) % candidates.length)
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActiveIndex((index) => (index - 1 + candidates.length) % candidates.length)
        return
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault()
        const candidate = candidates[activeIndex]
        if (candidate) selectMember(candidate)
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setQuery(null)
        return
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
  }

  const activeMentions = [...selected.keys()].filter((handle) => includesHandle(content, handle))

  return (
    <form className="composer" onSubmit={submit}>
      {replyTo ? (
        <div className="composer__reply">
          <CornerDownLeft size={15} aria-hidden="true" />
          <span>Réponse à <strong>{replyTo.author}</strong></span>
          <button type="button" aria-label="Annuler la réponse" onClick={onCancelReply}><X size={15} /></button>
        </div>
      ) : null}
      <div className="composer__field">
        <textarea
          ref={textarea}
          rows={2}
          aria-label="Écrire un message"
          aria-autocomplete="list"
          aria-controls={query && candidates.length > 0 ? 'mention-suggestions' : undefined}
          aria-activedescendant={query && candidates.length > 0 ? `mention-${activeIndex}` : undefined}
          placeholder={disabled ? 'Sélectionnez un channel pour écrire…' : 'Écrire un message… Utilisez @ pour mentionner un agent'}
          value={content}
          disabled={disabled}
          onChange={(event) => {
            setContent(event.target.value)
            updateQuery(event.target.value, event.target.selectionStart)
          }}
          onClick={(event) => updateQuery(content, event.currentTarget.selectionStart)}
          onKeyDown={onKeyDown}
        />
        {query && candidates.length > 0 ? (
          <div id="mention-suggestions" className="mention-menu" role="listbox" aria-label="Membres à mentionner">
            <div className="mention-menu__label"><AtSign size={14} /> Mentionner un membre</div>
            {candidates.map((member, index) => (
              <button
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                id={`mention-${index}`}
                key={member.actor_id}
                className={index === activeIndex ? 'is-active' : ''}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => selectMember(member)}
              >
                <span className={`avatar avatar--${member.actor_type}`}>@</span>
                <span><strong>@{member.handle}</strong><small>{member.display_name} · {member.role}</small></span>
                <em>{member.actor_type === 'agent' ? 'Agent' : 'Humain'}</em>
              </button>
            ))}
          </div>
        ) : null}
      </div>
      <div className="composer__footer">
        <div className="composer__mentions" aria-live="polite">
          {activeMentions.length > 0 ? (
            <><AtSign size={14} /> Mentions structurées : {activeMentions.map((handle) => `@${handle}`).join(', ')}</>
          ) : (
            <span><kbd>Entrée</kbd> envoyer · <kbd>⇧ Entrée</kbd> nouvelle ligne</span>
          )}
        </div>
        <Button type="submit" size="icon" isLoading={isSending} disabled={!content.trim() || disabled} aria-label="Envoyer le message">
          {!isSending ? <Send size={17} aria-hidden="true" /> : null}
        </Button>
      </div>
    </form>
  )
}
