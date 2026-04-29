#!/bin/bash
# Install local git hooks for homelable-hacs.
# Run once after cloning: ./scripts/install-hooks.sh
set -e

ROOT=$(git rev-parse --show-toplevel)
HOOK="$ROOT/.git/hooks/pre-commit"

cat > "$HOOK" <<'EOF'
#!/bin/bash
set -e

ROOT=$(git rev-parse --show-toplevel)
STAGED=$(git diff --cached --name-only --diff-filter=ACMR)

if echo "$STAGED" | grep -qE "^custom_components/|^tests/"; then
  echo "⚡ Python: ruff check..."
  cd "$ROOT"
  if [ -d ".venv" ]; then
    .venv/bin/ruff check custom_components/
    .venv/bin/python -m pytest -q
  else
    ruff check custom_components/
    echo "  (skipping pytest — no .venv; tests need HA installed. Run \`pip install -e \".[dev]\"\` in a venv to enable.)"
  fi
fi

if echo "$STAGED" | grep -q "^frontend-src/"; then
  echo "⚡ Frontend: lint + typecheck..."
  cd "$ROOT/frontend-src"
  if [ -d node_modules ]; then
    npm run lint --silent
    npm run typecheck --silent
  else
    echo "  (skipping — node_modules missing; run \`npm install\` in frontend-src/ to enable.)"
  fi
fi

echo "✅ All checks passed."
EOF

chmod +x "$HOOK"
echo "Installed pre-commit hook → $HOOK"
