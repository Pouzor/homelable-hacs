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
    plugins: [
      react(),
      tailwindcss(),
      ...(isHaBuild ? [cssInjectedByJsPlugin()] : []),
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
          assetFileNames: 'homelable-panel-[hash][extname]',
          inlineDynamicImports: true,
        },
      },
    }
  }

  return baseConfig
})
