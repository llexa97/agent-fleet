import { expect, test } from '@playwright/test'

const user = {
  user_id: '00000000-0000-4000-8000-000000000001',
  actor_id: '00000000-0000-4000-8000-000000000002',
  tenant_id: '00000000-0000-4000-8000-000000000003',
  email: 'axel@example.com',
  display_name: 'Axel',
  is_owner: true,
}
const space = {
  id: '10000000-0000-4000-8000-000000000001', tenant_id: user.tenant_id,
  name: 'Business', slug: 'business', kind: 'business', description: null,
  created_at: '2026-08-20T08:00:00Z',
}
const channel = {
  id: '20000000-0000-4000-8000-000000000001', tenant_id: user.tenant_id,
  space_id: space.id, name: 'client-taxi', slug: 'client-taxi', kind: 'project',
  description: 'Projet client Taxi', is_archived: false, created_at: '2026-08-20T08:00:00Z',
}
const members = [
  { actor_id: user.actor_id, actor_type: 'human', display_name: 'Axel', agent_id: null, handle: 'axel', role: 'Propriétaire' },
  { actor_id: '30000000-0000-4000-8000-000000000001', actor_type: 'agent', display_name: 'CTO', agent_id: '30000000-0000-4000-8000-000000000002', handle: 'cto', role: 'Responsable technique' },
]

test('un humain sélectionne une mention structurée dans un channel', async ({ page }) => {
  let postedBody: Record<string, unknown> | undefined
  const postedMessages: Array<Record<string, unknown>> = []
  await page.routeWebSocket('**/api/v1/events/ws', () => undefined)
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (pathname.endsWith('/auth/me')) return route.fulfill({ json: user })
    if (pathname.endsWith('/spaces')) return route.fulfill({ json: [space] })
    if (pathname.endsWith('/channels')) return route.fulfill({ json: [channel] })
    if (pathname.endsWith(`/channels/${channel.id}/members`)) return route.fulfill({ json: members })
    if (pathname.endsWith(`/channels/${channel.id}/messages`) && request.method() === 'GET') return route.fulfill({ json: postedMessages })
    if (pathname.endsWith(`/channels/${channel.id}/messages`) && request.method() === 'POST') {
      postedBody = request.postDataJSON() as Record<string, unknown>
      const response = {
        id: '40000000-0000-4000-8000-000000000001', tenant_id: user.tenant_id,
        space_id: space.id, channel_id: channel.id, thread_id: null,
        author_type: 'human', author_id: user.actor_id, author_display_name: 'Axel',
        author_handle: 'axel', content: postedBody.content, reply_to_id: null,
        trace_id: null, task_id: null, expects_response: true, is_technical: false,
        mentions: [], created_at: '2026-08-20T08:01:00Z',
      }
      postedMessages.push(response)
      return route.fulfill({ status: 201, json: response })
    }
    if (pathname.endsWith('/permissions')) return route.fulfill({ json: [] })
    return route.fulfill({ status: 404, json: { detail: 'Non simulé' } })
  })

  await page.goto('/channels')
  await expect(page.getByRole('heading', { name: 'client-taxi' })).toBeVisible()
  const composer = page.getByRole('textbox', { name: /écrire un message/i })
  await composer.fill('Bonjour @ct')
  await page.getByRole('option', { name: /@cto/i }).click()
  await composer.fill('Bonjour @cto fais avancer l’authentification')
  await page.getByRole('button', { name: /envoyer le message/i }).click()

  await expect.poll(() => postedBody).toMatchObject({
    content: 'Bonjour @cto fais avancer l’authentification',
    expects_response: true,
    mentions: [{ target_type: 'agent', target_id: members[1]?.agent_id, handle_at_creation: 'cto' }],
  })
  await expect(page.getByText('Bonjour @cto fais avancer l’authentification')).toBeVisible()
})

test('OpenCode est proposé comme harness lors de la création d’un agent', async ({ page }) => {
  await page.routeWebSocket('**/api/v1/events/ws', () => undefined)
  await page.route('**/api/v1/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname.endsWith('/auth/me')) return route.fulfill({ json: user })
    if (pathname.endsWith('/spaces')) return route.fulfill({ json: [space] })
    if (pathname.endsWith('/agents')) return route.fulfill({ json: [] })
    if (pathname.endsWith('/workers')) return route.fulfill({ json: [] })
    if (pathname.endsWith('/channels')) return route.fulfill({ json: [] })
    if (pathname.endsWith('/permissions')) return route.fulfill({ json: [] })
    return route.fulfill({ status: 404, json: { detail: 'Non simulé' } })
  })

  await page.goto('/agents')
  await page.getByRole('button', { name: 'Nouvel agent' }).click()
  const harness = page.getByLabel('Harness')
  await expect(harness).toHaveValue('codex')
  await expect(harness.getByRole('option', { name: /Fake ACP/ })).toHaveCount(0)
  await harness.selectOption('opencode')

  await expect(harness).toHaveValue('opencode')
  await expect(harness.getByRole('option', { name: 'OpenCode ACP' })).toHaveCount(1)
})
