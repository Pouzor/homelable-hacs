/**
 * HA build re-exports the WS adapter under the legacy module name so existing
 * `import { canvasApi, scanApi, ... } from '@/api/client'` paths keep working.
 */
export * from './ha'
