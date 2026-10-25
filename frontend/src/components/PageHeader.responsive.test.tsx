// @vitest-environment jsdom
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import { PageHeader } from './PageHeader'

function headerClasses(markup: string) {
  const match = markup.match(/<header class="([^"]+)"/)
  if (!match) throw new Error('PageHeader did not render a header')
  return match[1].split(/\s+/)
}

it('reserves the mobile menu gutter and keeps wrapped tools inside the header', () => {
  const markup = renderToStaticMarkup(
    <PageHeader title="研究工具" right={<><button>刷新</button><button>设置</button></>} />,
  )
  const rootClasses = headerClasses(markup)
  const host = document.createElement('div')
  host.innerHTML = markup
  const toolGroup = host.querySelector('.ml-auto')

  expect(rootClasses).toContain('pl-14')
  expect(rootClasses).toContain('pr-4')
  expect(rootClasses).toContain('md:pl-5')
  expect(rootClasses).toContain('md:pr-5')
  expect(toolGroup?.classList.contains('flex-wrap')).toBe(true)
  expect(toolGroup?.classList.contains('max-w-full')).toBe(true)
  expect(toolGroup?.classList.contains('[&>div]:flex-wrap')).toBe(true)
  expect(markup).toContain('刷新')
  expect(markup).toContain('设置')
})

it('merges the existing page-level mobile gutter instead of duplicating it', () => {
  const markup = renderToStaticMarkup(<PageHeader title="研究资料" className="pl-14 sm:pl-5" />)
  const rootClasses = headerClasses(markup)

  expect(rootClasses.filter(className => className === 'pl-14')).toHaveLength(1)
  expect(rootClasses).not.toContain('sm:pl-5')
  expect(rootClasses).toContain('md:pl-5')
})

it('keeps the mobile title gutter when a page supplies its own horizontal padding', () => {
  const markup = renderToStaticMarkup(<PageHeader title="因子" className="px-3 lg:px-5" />)
  const rootClasses = headerClasses(markup)

  expect(rootClasses).toContain('pl-14')
  expect(rootClasses).toContain('pr-3')
})
