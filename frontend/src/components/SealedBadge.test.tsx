// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { expect, it } from 'vitest'
import { SealedBadge } from './SealedBadge'

it('retains the quote total and labels missing book evidence separately', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  const host = document.createElement('div')
  document.body.append(host)
  const root = createRoot(host)
  const client = new QueryClient()
  try {
    await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}>
      <SealedBadge degraded={false} hasDepth isHistorical={false} sealedReady
        rawUp={36} rawDown={28}
        sealedCountsUp={{ real: 14, fake: 0, pending: 22 }}
        sealedCountsDown={{ real: 13, fake: 0, pending: 15 }} />
    </QueryClientProvider></MemoryRouter>))
    await act(async () => host.querySelector('button')!.click())
    expect(document.body.textContent).toContain('价格触及 36')
    expect(document.body.textContent).toContain('价格触及 28')
    expect(document.body.textContent).toContain('待核对 15')
  } finally {
    await act(async () => root.unmount())
    client.clear()
    host.remove()
  }
})
