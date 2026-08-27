/**
 * Lazy half of the Lovelace card: everything that pulls React, Tailwind and
 * the canvas in. `ha-card.ts` imports this only once a card is actually on
 * the dashboard, so pages without one pay nothing beyond the tiny entry.
 */
import { StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { CardShell } from '@/components/card/CardShell'
import { HassContext, setHass, type Hass } from './hass'
import { ShadowRootContext } from './portal'
import { injectShadowStyles } from './shadowCss'
import type { HomelableCardConfig } from './cardConfig'

export interface CardRenderState {
  /** False when another Homelable card on the page already owns the store. */
  primary: boolean
}

export interface CardMount {
  render: (hass: Hass, config: HomelableCardConfig, state: CardRenderState) => void
  unmount: () => void
}

/** Attach a React root to the card's shadow root and return its handle. */
export function mountCard(shadow: ShadowRoot): CardMount {
  injectShadowStyles(shadow)

  let mountPoint = shadow.querySelector<HTMLDivElement>('div[data-homelable-card]')
  if (!mountPoint) {
    mountPoint = document.createElement('div')
    mountPoint.setAttribute('data-homelable-card', '')
    mountPoint.style.cssText = 'width: 100%;'
    shadow.appendChild(mountPoint)
  }
  const point = mountPoint
  let root: Root | null = createRoot(point)

  return {
    render(hass, config, state) {
      // api/ha.ts calls through the module-level singleton, not through props.
      setHass(hass)
      root?.render(
        <StrictMode>
          <HassContext.Provider value={hass}>
            <ShadowRootContext.Provider value={point}>
              <CardShell config={config} primary={state.primary} />
            </ShadowRootContext.Provider>
          </HassContext.Provider>
        </StrictMode>
      )
    },
    unmount() {
      root?.unmount()
      root = null
    },
  }
}
