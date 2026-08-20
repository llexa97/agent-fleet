import { describe, expect, it } from 'vitest'
import { itemsFrom, websocketUrl } from './api'

describe('client API', () => {
  it('normalise les tableaux et les pages', () => {
    expect(itemsFrom([1, 2])).toEqual([1, 2])
    expect(itemsFrom({ items: ['a'], total: 1 })).toEqual(['a'])
  })

  it('construit une URL WebSocket sur le même hôte', () => {
    expect(new URL(websocketUrl()).pathname).toBe('/api/v1/events/ws')
    expect(new URL(websocketUrl()).protocol).toMatch(/^ws/)
  })
})
