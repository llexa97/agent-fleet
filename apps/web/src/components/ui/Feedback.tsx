import { AlertTriangle, Inbox, RefreshCw } from 'lucide-react'
import { Button } from './Button'

export function LoadingState({ label = 'Chargement…' }: { label?: string }) {
  return (
    <div className="state state--loading" role="status">
      <span className="loading-dot" />
      <span>{label}</span>
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : 'Une erreur inattendue est survenue.'
  return (
    <div className="state state--error" role="alert">
      <AlertTriangle aria-hidden="true" size={21} />
      <div>
        <strong>Impossible de charger cette vue</strong>
        <p>{message}</p>
      </div>
      {onRetry ? (
        <Button variant="secondary" size="small" icon={RefreshCw} onClick={onRetry}>
          Réessayer
        </Button>
      ) : null}
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action?: React.ReactNode
}) {
  return (
    <div className="empty-state">
      <span className="empty-state__icon">
        <Inbox aria-hidden="true" size={24} />
      </span>
      <strong>{title}</strong>
      <p>{description}</p>
      {action}
    </div>
  )
}

export function InlineError({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <p className="inline-error" role="alert">
      {error instanceof Error ? error.message : 'Une erreur est survenue.'}
    </p>
  )
}
