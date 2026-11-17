/** An HTML comment is for the source, never printed on the page. */
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'

import { Markdown } from '../markdown/Markdown'
import { isCommentOnly } from '../markdown/comments'

describe('Markdown — HTML comments', () => {
  it('hides the migration marker above migrated notes', () => {
    const { container } = render(
      <Markdown body={"## Notes\n\n<!-- Migrated verbatim from this device's Notes field on 2026-11-16. -->\n\nSpare disk in the drawer\n"} />,
    )
    expect(container.textContent).not.toContain('Migrated verbatim')
    expect(container.textContent).not.toContain('<!--')
    expect(screen.getByText('Spare disk in the drawer')).toBeDefined()
  })

  it('hides an inline comment inside a paragraph', () => {
    const { container } = render(<Markdown body={'Before <!-- hidden --> after'} />)
    expect(container.textContent).not.toContain('hidden')
    expect(container.textContent).toContain('Before')
    expect(container.textContent).toContain('after')
  })

  it('only matches nodes that are nothing but comments', () => {
    expect(isCommentOnly({ type: 'html', value: '<!-- a -->' })).toBe(true)
    expect(isCommentOnly({ type: 'html', value: '<!-- a -->\n<!-- b -->' })).toBe(true)
    expect(isCommentOnly({ type: 'html', value: '<b>bold</b>' })).toBe(false)
    expect(isCommentOnly({ type: 'text', value: '<!-- a -->' })).toBe(false)
    expect(isCommentOnly({ type: 'html', value: '<!-- a --> <b>x</b>' })).toBe(false)
    expect(isCommentOnly({ type: 'html', value: '<!-- unterminated' })).toBe(false)
    expect(isCommentOnly({ type: 'html', value: '   ' })).toBe(false)
  })

  it('stays linear on many back-to-back comments', () => {
    const value = '<!--' + '--><!--'.repeat(50_000) + '-->x'
    const start = performance.now()
    expect(isCommentOnly({ type: 'html', value })).toBe(false)
    expect(performance.now() - start).toBeLessThan(500)
  })
})
