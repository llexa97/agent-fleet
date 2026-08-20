import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ChannelMember } from '../../types/api'
import { MentionComposer } from './MentionComposer'

const members: ChannelMember[] = [
  {
    actor_id: 'actor-cto',
    actor_type: 'agent',
    display_name: 'CTO',
    agent_id: 'agent-cto',
    handle: 'cto',
    role: 'Responsable technique',
  },
  {
    actor_id: 'actor-axel',
    actor_type: 'human',
    display_name: 'Axel',
    agent_id: null,
    handle: 'axel',
    role: 'Propriétaire',
  },
]

describe('MentionComposer', () => {
  it('résout une sélection @ en mention structurée', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn().mockResolvedValue(undefined)
    render(<MentionComposer members={members} isSending={false} onSend={onSend} />)

    const textarea = screen.getByRole('textbox', { name: /écrire un message/i })
    await user.type(textarea, 'Bonjour @ct')
    await user.click(screen.getByRole('option', { name: /@cto/i }))
    await user.type(textarea, 'peux-tu vérifier ?')

    expect(screen.getByText(/mentions structurées : @cto/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /envoyer le message/i }))

    expect(onSend).toHaveBeenCalledWith({
      content: 'Bonjour @cto peux-tu vérifier ?',
      expects_response: true,
      mentions: [
        {
          target_type: 'agent',
          target_id: 'agent-cto',
          handle_at_creation: 'cto',
        },
      ],
    })
  })

  it('ne transforme pas un @ saisi sans sélection structurée', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn().mockResolvedValue(undefined)
    render(<MentionComposer members={members} isSending={false} onSend={onSend} />)

    const textarea = screen.getByRole('textbox', { name: /écrire un message/i })
    await user.type(textarea, 'Citation de @cto')
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: /envoyer le message/i }))

    expect(onSend).toHaveBeenCalledWith({
      content: 'Citation de @cto',
      expects_response: false,
      mentions: [],
    })
  })
})
