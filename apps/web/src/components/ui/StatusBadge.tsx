const positive = new Set(['active', 'connected', 'completed', 'online', 'ready', 'approved'])
const warning = new Set([
  'busy',
  'queued',
  'pending',
  'processing',
  'running',
  'waiting',
  'waiting_input',
  'waiting_approval',
  'review',
  'connecting',
])
const negative = new Set(['failed', 'error', 'offline', 'cancelled', 'revoked', 'denied', 'blocked'])

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const normalized = status.toLowerCase()
  const tone = positive.has(normalized)
    ? 'positive'
    : warning.has(normalized)
      ? 'warning'
      : negative.has(normalized)
        ? 'negative'
        : 'neutral'
  return (
    <span className={`status status--${tone}`}>
      <span className="status__dot" aria-hidden="true" />
      {label ?? status.replaceAll('_', ' ')}
    </span>
  )
}
