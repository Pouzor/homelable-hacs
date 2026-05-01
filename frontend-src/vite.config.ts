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
      // HA mounts panels inside its own shadow DOM; document.head <style>
      // never reaches us. Stash CSS-module output on window.__HOMELABLE_CSS__
      // so ha-panel.tsx can inject it into our own shadow root.
      // (Tailwind + index.css/App.css are pulled in via `?inline` imports in
      // ha-panel.tsx — @tailwindcss/vite emits CSS through a path this plugin
      // doesn't intercept.)
      ...(isHaBuild
        ? [
            cssInjectedByJsPlugin({
              injectCodeFunction: function injectStashCode(cssCode: string) {
                ;(window as unknown as Record<string, string>)
                  .__HOMELABLE_CSS__ = cssCode
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
        input: path.resolve(__dirname, 'src/main.tsx'),
        output: {
          format: 'es',
          entryFileNames: 'homelable-panel-[hash].js',
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
