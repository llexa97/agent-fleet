import { Bot, ChevronDown, CornerDownLeft, GitBranch, Wrench } from 'lucide-react'
import { Fragment, useState } from 'react'
import { formatDateTime, initials } from '../../lib/format'
import type { Message } from '../../types/api'

function highlightedContent(message: Message) {
  const handles = [...new Set(message.mentions.map((mention) => mention.handle_at_creation))]
  if (handles.length === 0) return message.content
  const escaped = handles.map((handle) => handle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const pattern = new RegExp(`(@(?:${escaped.join('|')}))`, 'gi')
  return message.content.split(pattern).map((part, index) => {
    const isMention = part.startsWith('@') && handles.some((handle) => `@${handle}`.toLowerCase() === part.toLowerCase())
    return isMention ? <mark key={`${part}-${index}`} className="mention">{part}</mark> : <Fragment key={`${part}-${index}`}>{part}</Fragment>
  })
}

export function MessageItem({ message, onReply }: { message: Message; onReply: (message: Message) => void }) {
  const [technicalOpen, setTechnicalOpen] = useState(false)
  const author = message.author_display_name ?? message.author_handle ?? message.author_type
  const isAgent = message.author_type === 'agent'

  if (message.is_technical) {
    return (
      <article className="technical-event">
        <button type="button" aria-expanded={technicalOpen} onClick={() => setTechnicalOpen((open) => !open)}>
          <Wrench size={15} aria-hidden="true" />
          <span>Événement technique · {author}</span>
          <time dateTime={message.created_at}>{formatDateTime(message.created_at)}</time>
          <ChevronDown className={technicalOpen ? 'rotate' : ''} size={15} />
        </button>
        {technicalOpen ? <pre>{message.content}</pre> : null}
      </article>
    )
  }

  return (
    <article className={`message message--${message.author_type}`} data-message-id={message.id}>
      <span className={`avatar avatar--${message.author_type}`} aria-hidden="true">
        {isAgent ? <Bot size={17} /> : initials(author)}
      </span>
      <div className="message__body">
        <header>
          <strong>{author}</strong>
          {message.author_handle ? <span>@{message.author_handle}</span> : null}
          {isAgent ? <em>Agent</em> : null}
          <time dateTime={message.created_at} title={formatDateTime(message.created_at)}>
            {new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' }).format(new Date(message.created_at))}
          </time>
        </header>
        <p>{highlightedContent(message)}</p>
        <footer>
          <button type="button" onClick={() => onReply(message)}><CornerDownLeft size={14} /> Répondre</button>
          {message.trace_id ? <a href={`/traces?selected=${message.trace_id}`}><GitBranch size={14} /> Voir la trace</a> : null}
          {message.task_id ? <a href={`/tasks?selected=${message.task_id}`}>Tâche liée</a> : null}
        </footer>
      </div>
    </article>
  )
}
