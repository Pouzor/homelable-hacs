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
  echo "⚡ Python: ruff + pytest..."
  cd "$ROOT"
  if [ -d ".venv" ]; then
    .venv/bin/ruff check custom_components/
    .venv/bin/python -m pytest -q
  else
    ruff check custom_components/
    python -m pytest -q
  fi
fi

if echo "$STAGED" | grep -q "^frontend-src/"; then
  echo "⚡ Frontend: lint + typecheck + tests..."
  cd "$ROOT/frontend-src"
  npm run lint --silent
  npm run typecheck --silent
  npm test -- --run 2>&1 | tail -10
fi

echo "✅ All checks passed."
EOF

chmod +x "$HOOK"
echo "Installed pre-commit hook → $HOOK"
