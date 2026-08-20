export function formatRelativeDate(value: string | null): string {
  if (!value) return 'Jamais'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Date inconnue'
  const deltaSeconds = Math.round((date.getTime() - Date.now()) / 1000)
  const abs = Math.abs(deltaSeconds)
  const formatter = new Intl.RelativeTimeFormat('fr', { numeric: 'auto' })
  if (abs < 60) return formatter.format(deltaSeconds, 'second')
  if (abs < 3_600) return formatter.format(Math.round(deltaSeconds / 60), 'minute')
  if (abs < 86_400) return formatter.format(Math.round(deltaSeconds / 3_600), 'hour')
  return formatter.format(Math.round(deltaSeconds / 86_400), 'day')
}

export function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('fr-FR', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat('fr-FR', { notation: 'compact' }).format(value)
}

export function formatMoney(value: number | string): string {
  const amount = typeof value === 'number' ? value : Number.parseFloat(value)
  return new Intl.NumberFormat('fr-FR', {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 2,
  }).format(Number.isFinite(amount) ? amount : 0)
}

export function slugify(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 100)
}

export function initials(value: string): string {
  return value
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join('')
}
