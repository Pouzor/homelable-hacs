import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Markdown } from '../markdown/Markdown'

describe('Markdown — uploaded media', () => {
  it('shows an uploaded image from its server URL', () => {
    render(<Markdown body="![topology](/homelable_media/abc.png)" />)
    expect(screen.getByRole('img', { name: 'topology' })).toHaveAttribute('src', '/homelable_media/abc.png')
  })

  it('opens an uploaded file beside the page', () => {
    render(<Markdown body="[manual.pdf](/homelable_media/abc.pdf)" />)
    const link = screen.getByRole('link', { name: 'manual.pdf' })
    expect(link).toHaveAttribute('href', '/homelable_media/abc.pdf')
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('still follows an in-page link in place', () => {
    render(<Markdown body="[Services](#services)" />)
    expect(screen.getByRole('link', { name: 'Services' })).not.toHaveAttribute('target')
  })
})
