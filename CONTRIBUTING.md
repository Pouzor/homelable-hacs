# Contributing to Homelable for Home Assistant

Thanks for taking the time to contribute! This document covers everything you
need to get started on the **HACS integration** of Homelable. The standalone
version lives in [`Pouzor/homelable`](https://github.com/Pouzor/homelable) and
has its own contributing guide.

---

## Table of Contents

- [Ways to Contribute](#ways-to-contribute)
- [Reporting Bugs](#reporting-bugs)
- [Suggesting Features](#suggesting-features)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Commit Message Format](#commit-message-format)

---

## Ways to Contribute

- Report bugs or unexpected behavior
- Suggest new features or improvements
- Fix open issues (check the [issue tracker](https://github.com/Pouzor/homelable-hacs/issues))
- Improve documentation (`README.md`, this file, in-code comments)
- Add service signatures to `custom_components/homelable/service_signatures.json`
- Translate strings under `custom_components/homelable/translations/`

---

## Reporting Bugs

Before opening an issue, search existing ones to avoid duplicates.

When filing a bug, include:

- **Homelable integration version** (visible in the panel's bottom-left)
- **Home Assistant version** (Settings → About)
- **HA install type** (HAOS / Supervised / Container / Core)
- **Install method** (HACS / manual)
- **Steps to reproduce**
- **Expected vs actual behavior**
- **Relevant HA logs** (Settings → System → Logs, filter for `homelable`)
- **Browser console errors** if it's a UI / panel issue

---

## Suggesting Features

Open an issue with the `enhancement` label. Describe:

- The problem you're trying to solve
- Your proposed solution
- Any alternatives you considered

For large changes, discuss first before writing code — it avoids wasted effort.

---

## Development Setup


```bash
# Python (3.12 preferred; 3.13 works)
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip uninstall -y homelable-hacs

# Frontend
cd frontend-src
npm install
```

Run the integration against a real Home Assistant in Docker:

```bash
./scripts/dev-ha.sh           # build frontend + start HA on :8123 + tail logs
./scripts/dev-ha.sh restart   # rebuild frontend + restart container
./scripts/dev-ha.sh logs      # tail logs
./scripts/dev-ha.sh shell     # bash inside the container
```

After onboarding at <http://localhost:8123>, add the integration via
**Settings → Devices & Services → Add Integration → Homelable**.

---

## Project Structure

```
homelable-hacs/
├── custom_components/homelable/    # HA integration (Python)
│   ├── __init__.py                 # Setup / unload entry points
│   ├── manifest.json               # HACS / HA metadata
│   ├── const.py                    # Domain, defaults, storage keys
│   ├── config_flow.py              # UI setup wizard + options flow
│   ├── coordinator.py              # DataUpdateCoordinator (scanner + status)
│   ├── panel.py                    # Lovelace panel registration
│   ├── websocket.py                # WS command handlers
│   ├── scanner.py                  # nmap-driven discovery
│   ├── fingerprint.py              # Service detection from open ports
│   ├── status_checker.py           # ping / http / tcp / ssh checks
│   ├── service_signatures.json     # Port → service mappings
│   ├── translations/               # en.json, fr.json
│   └── frontend/                   # Built panel bundle (gitignored)
├── frontend-src/                   # React + Vite source for the panel
├── tests/                          # pytest-homeassistant-custom-component
└── .github/workflows/              # hassfest, hacs/action, release
```

---

## Coding Standards

### General

- No untested code merged — every feature or fix must include tests.
- Keep changes focused — one concern per PR.
- Follow existing patterns; don't introduce a new state-management or
  styling approach without discussion.

### Frontend (TypeScript + React)

- Strict TypeScript — no `any`, no type assertions unless truly necessary.
- React Flow node domain fields go in `node.data`, never on the node root.
- State via Zustand stores — no prop drilling beyond 2 levels.
- Styling via Tailwind utility classes — follow the existing
  [design system](#design-system).
- The panel runs inside a Shadow DOM. CSS imports in `ha-panel.tsx` use the
  `?inline` suffix; don't move them to global imports.

### Backend (Python + Home Assistant)

- Python 3.12+ syntax.
- Use Home Assistant primitives — `Store`, `DataUpdateCoordinator`,
  `websocket_api`, `frontend.async_register_built_in_panel`.
- **Never** reintroduce FastAPI, SQLAlchemy, Alembic, or JWT — those belong
  in the standalone repo.
- Anything blocking (nmap subprocess, file I/O on a big payload) goes through
  `async_add_executor_job` or `asyncio.to_thread`. Never block the event loop.
- All persistence via `homeassistant.helpers.storage.Store`.
- Run before committing:
  ```bash
  ruff check custom_components/
  .venv/bin/pytest
  ```

### Design System

| Token | Value |
|---|---|
| Background | `#0d1117` |
| Surface | `#161b22` |
| Card | `#21262d` |
| Accent cyan | `#00d4ff` |
| Online | `#39d353` |
| Offline | `#f85149` |
| Pending | `#e3b341` |
| Font (UI) | Inter |
| Font (IPs / ports) | JetBrains Mono |

---

## Testing

Tests run automatically via a pre-commit hook when matching files are staged.

### Python

```bash
.venv/bin/pytest                                  # full suite
.venv/bin/pytest tests/test_websocket.py          # one file
.venv/bin/pytest -k trigger_scan -v               # by name
```

Test files live under `tests/test_*.py`. `pytest` is configured with
`asyncio_mode=auto`, so async tests do not need `@pytest.mark.asyncio`.

**What to cover**: config flow happy path + error cases, coordinator updates,
WebSocket commands (including the `require_admin` gate), scanner /
fingerprint / status helpers. See `tests/conftest.py` for the autouse
storage isolation fixture and `tests/test_websocket.py::setup_ws` for the WS
test pattern.

### Frontend

```bash
cd frontend-src
npm test                  # run all tests
npm run test:coverage     # with coverage report
```

Test files live in `__tests__/` next to their module, named `*.test.ts(x)`.

**What to cover**: Zustand store actions, utility functions, non-trivial
component logic. Avoid testing standalone-only paths (the integration runs
in HA mode only).

---

## Submitting a Pull Request

1. **Fork** the repo and create a branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```

2. **Make your changes** — include tests.

3. **Run the full test suite** (Python + frontend) and make sure everything
   passes locally. Then `ruff check custom_components/` and
   `npm run lint && npm run typecheck` in `frontend-src/`.

4. **Open a PR** against `main`:
   - Use a clear title (see commit format below)
   - Describe what changed and why
   - Reference any related issues (`Closes #123`)
   - Include screenshots / GIFs for UI changes

5. CI (`Quality` and `Validate`) must be green before merge. The `Validate`
   workflow runs `hassfest` and the HACS validator.

6. Keep PRs focused — one feature or fix per PR. Large refactors should be
   discussed in an issue first.

---

## Commit Message Format

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <short description>

[optional body]
```

| Type | When to use |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change with no behavior change |
| `test` | Adding or fixing tests |
| `chore` | Build, deps, tooling, dev container |
| `ci` | CI/CD configuration |

**Examples:**

```
feat: lazy-load React Flow chunk
fix(security): require_admin on mutating WS commands
docs: update CONTRIBUTING for HACS context
chore(dev): bundle nmap-scripts in dev container
```

---

## Questions?

Open a [GitHub Discussion](https://github.com/Pouzor/homelable-hacs/discussions)
or drop a comment on a relevant issue.
