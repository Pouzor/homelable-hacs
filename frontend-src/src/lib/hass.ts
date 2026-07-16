/**
 * Home Assistant connection bridge.
 *
 * The custom element receives `hass` as a property from HA. We mirror it into
 * a module-level slot so non-React code (e.g. `api/client.ts`) can call into it
 * without prop-drilling. Updates are pushed via subscribers.
 */
import { createContext, useContext, useEffect, useState } from 'react'

export interface HassConnection {
  sendMessagePromise: <T = unknown>(msg: Record<string, unknown>) => Promise<T>
  subscribeMessage: <T = unknown>(
    callback: (msg: T) => void,
    subscribeMessage: Record<string, unknown>
  ) => Promise<() => void>
}

export interface Hass {
  connection: HassConnection
  language?: string
  themes?: { darkMode: boolean }
  user?: { id: string; name: string; is_admin: boolean }
  // HA auth object. Used for authenticated HTTP calls (media upload) that can't
  // go over the WS channel — `accessToken` is the current bearer token.
  auth?: {
    accessToken?: string
    data?: { access_token?: string }
  }
}

let _hass: Hass | null = null
const _listeners = new Set<(hass: Hass | null) => void>()

export function setHass(hass: Hass | null) {
  _hass = hass
  for (const cb of _listeners) cb(hass)
}

export function getHass(): Hass {
  if (!_hass) {
    throw new Error(
      'Home Assistant connection not yet available. Did the panel mount?'
    )
  }
  return _hass
}

export function tryGetHass(): Hass | null {
  return _hass
}

export const HassContext = createContext<Hass | null>(null)

export function useHass(): Hass {
  const ctx = useContext(HassContext)
  if (!ctx) throw new Error('useHass must be used inside <HassProvider>')
  return ctx
}

export function useHassListener() {
  const [hass, setLocalHass] = useState<Hass | null>(_hass)
  useEffect(() => {
    const cb = (h: Hass | null) => setLocalHass(h)
    _listeners.add(cb)
    return () => {
      _listeners.delete(cb)
    }
  }, [])
  return hass
}

export async function wsCall<T = unknown>(
  type: string,
  extra: Record<string, unknown> = {}
): Promise<T> {
  return getHass().connection.sendMessagePromise<T>({ type, ...extra })
}

export async function wsSubscribe<T = unknown>(
  type: string,
  callback: (msg: T) => void,
  extra: Record<string, unknown> = {}
): Promise<() => void> {
  return getHass().connection.subscribeMessage<T>(callback, {
    type,
    ...extra,
  })
}
