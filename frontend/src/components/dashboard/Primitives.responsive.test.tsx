import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import { KpiCell } from './Primitives'

it('keeps all parts of a composite KPI visible and allows wrapping', () => {
  const markup = renderToStaticMarkup(
    <KpiCell
      label="个股涨 / 平 / 跌"
      value={<><span className="text-bull">4524</span><span>/</span><span>139</span><span>/</span><span>908</span></>}
    />,
  )
  const valueClasses = [...markup.matchAll(/<div class="([^"]+)"/g)].map(match => match[1])[2]
  const text = markup.replace(/<[^>]+>/g, '')

  expect(valueClasses).toContain('whitespace-normal')
  expect(valueClasses).toContain('break-words')
  expect(valueClasses).not.toContain('truncate')
  expect(text).toContain('4524/139/908')
})

it('retains truncation and the title hint for plain KPI values', () => {
  const markup = renderToStaticMarkup(<KpiCell label="成交额" value="123456789012345" />)
  const valueElement = markup.match(/<div title="123456789012345" class="([^"]+)"/)

  expect(valueElement?.[1]).toContain('truncate')
})
