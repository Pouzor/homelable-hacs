# Homelable HA Frontend

React + React Flow source for the Home Assistant Lovelace panel.

> **Source-of-truth note:** the React app is ported from `homelable/frontend/src/`. After porting, the two diverge:
> - This version drops auth/login (HA handles it).
> - API client is `src/api/ha.ts` (HA WebSocket via `hass.connection`).
> - Build output = single bundled JS for HA panel registration.

## Build Targets

```bash
npm run dev          # local dev (point at local HA via reverse proxy or mock)
npm run build:ha     # produce bundle → ../custom_components/homelable/frontend/
npm test             # vitest
npm run lint
npm run typecheck
```

## Bundle Output

Vite is configured to output a single self-contained JS file with hashed filename:

```
../custom_components/homelable/frontend/homelable-panel-{hash}.js
```

The integration's `panel.py` discovers this file by glob and registers it as the panel module.

## Custom Element

`src/main.tsx` registers `<homelable-panel>` as a custom element. HA mounts it and pushes:
- `hass` property — full HA connection + state
- `narrow`, `route`, `panel` properties

The element bridges these into the React tree (props provider) so existing components don't care they're inside a custom element.

