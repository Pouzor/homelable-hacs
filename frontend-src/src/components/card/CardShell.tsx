/**
 * Chrome around the card's canvas: the `ha-card` look, the optional header and
 * the fixed-height body.
 *
 * We render inside our own shadow root rather than HA's `<ha-card>`, so the
 * card surface is rebuilt from HA's own CSS custom properties — they pierce
 * the shadow boundary and keep the card in step with the active HA theme.
 *
 * The canvas itself lands next (see TODO-002 step 4).
 */
import type { ReactNode } from 'react'
import type { HomelableCardConfig } from '@/lib/cardConfig'

interface CardShellProps {
  config: HomelableCardConfig
  /** False when another Homelable card already owns the canvas store. */
  primary: boolean
  children?: ReactNode
}

const surface: React.CSSProperties = {
  background: 'var(--ha-card-background, var(--card-background-color, #fff))',
  borderRadius: 'var(--ha-card-border-radius, 12px)',
  border: 'var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color, transparent))',
  boxShadow: 'var(--ha-card-box-shadow, none)',
  color: 'var(--primary-text-color, #212121)',
  overflow: 'hidden',
}

export function CardShell({ config, primary, children }: CardShellProps) {
  return (
    <div style={surface}>
      {config.title ? (
        <div
          style={{
            padding: '12px 16px 8px',
            fontSize: '1.25rem',
            fontWeight: 400,
            lineHeight: 1.2,
            color: 'var(--ha-card-header-color, var(--primary-text-color, #212121))',
          }}
        >
          {config.title}
        </div>
      ) : null}
      <div style={{ height: `${config.height}px`, position: 'relative' }}>
        {primary ? children : <SecondaryNotice />}
      </div>
    </div>
  )
}

/**
 * Shown on every Homelable card after the first on a view. The canvas store is
 * a module singleton shared by the node components, so a second canvas would
 * overwrite the first one's data instead of rendering its own design.
 */
function SecondaryNotice() {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        padding: '16px',
        textAlign: 'center',
        fontSize: '0.875rem',
        color: 'var(--secondary-text-color, #727272)',
      }}
    >
      Only one Homelable card can be shown per dashboard view. Move this card to
      another view to display its canvas.
    </div>
  )
}
