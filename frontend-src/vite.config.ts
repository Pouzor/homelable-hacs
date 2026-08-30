import path from 'path'
import { defineConfig, type UserConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import cssInjectedByJsPlugin from 'vite-plugin-css-injected-by-js'

const HA_OUT_DIR = path.resolve(
  __dirname,
  '../custom_components/homelable/frontend'
)

export default defineConfig(({ mode }) => {
  const isHaBuild = mode === 'ha'

  const baseConfig: UserConfig = {
    define: {
      // Standalone reads VERSION from disk; HA build uses package.json or a placeholder.
      __APP_VERSION__: JSON.stringify(process.env.npm_package_version ?? '0.0.0'),
    },
    plugins: [
      react(),
      tailwindcss(),
      // HA mounts our elements inside its own shadow DOM; document.head <style>
      // never reaches us. Stash CSS-module output on window.__HOMELABLE_CSS__
      // so lib/shadowCss.ts can inject it into our own shadow root.
      // (Tailwind + index.css/App.css are pulled in via `?inline` imports in
      // lib/shadowCss.ts — @tailwindcss/vite emits CSS through a path this
      // plugin doesn't intercept.)
      // Append rather than assign: the panel and the card are separate entries
      // and HA loads the card module on every page, so both can run this on the
      // same document. Skipping already-present CSS keeps repeat loads cheap.
      ...(isHaBuild
        ? [
            cssInjectedByJsPlugin({
              injectCodeFunction: function injectStashCode(cssCode: string) {
                const w = window as unknown as Record<string, string | undefined>
                const current = w.__HOMELABLE_CSS__ ?? ''
                if (current.includes(cssCode)) return
                w.__HOMELABLE_CSS__ = current + cssCode
              },
            }),
          ]
        : []),
    ],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      coverage: {
        provider: 'v8',
        reporter: ['text', 'lcov'],
        exclude: ['src/components/ui/**', 'src/test/**'],
      },
    },
    server: {
      proxy: {
        '/api': 'http://localhost:8000',
        '/ws': { target: 'ws://localhost:8000', ws: true },
      },
    },
  }

  if (isHaBuild) {
    // HA serves the integration's frontend dir at /homelable_files/ (see const.py PANEL_URL).
    // Font/asset URLs in the bundle must use that absolute base.
    baseConfig.base = '/homelable_files/'
    baseConfig.build = {
      outDir: HA_OUT_DIR,
      emptyOutDir: true,
      sourcemap: false,
      cssCodeSplit: false,
      rollupOptions: {
        // Two entries: the full panel (registered by panel.py as a `custom`
        // panel) and the Lovelace card (loaded on every HA page through
        // frontend.add_extra_js_url). Shared deps land in the chunks below.
        input: {
          panel: path.resolve(__dirname, 'src/main.tsx'),
          card: path.resolve(__dirname, 'src/ha-card.ts'),
        },
        output: {
          format: 'es',
          entryFileNames: (chunk) =>
            chunk.name === 'card'
              ? 'homelable-card-[hash].js'
              : 'homelable-panel-[hash].js',
          chunkFileNames: 'homelable-chunk-[hash].js',
          assetFileNames: 'homelable-panel-[hash][extname]',
          manualChunks(id: string) {
            if (id.includes('node_modules/@xyflow') || id.includes('node_modules/d3-')) {
              return 'reactflow'
            }
            if (id.includes('node_modules/html-to-image') || id.includes('node_modules/jspdf') || id.includes('node_modules/dompurify')) {
              return 'export'
            }
            if (id.includes('node_modules/dagre') || id.includes('node_modules/@dagrejs')) {
              return 'layout'
            }
          },
        },
      },
    }
  }

  return baseConfig
})
