# Frontend source code (React 19 + Vite 8 + Tailwind CSS 4)

This is a clean copy of `C:\WorkForce_AI_Pro\frontend` (53 files). The original project was only read.

| Path | Content |
|---|---|
| `frontend/src/main.jsx`, `App.jsx` | Router and providers; lazy-loaded routes wrapped in `RequireAuth` / `RequireRole` |
| `frontend/src/api/client.js` | Shared Axios client: bearer token, session-expiry handling, error messages, downloads |
| `frontend/src/auth/` | Session state (`AuthContext.jsx`, `context.js`) |
| `frontend/src/pages/` | The 19 pages plus the login page |
| `frontend/src/components/` | Design-system components (`ui.jsx`), layout, guards, toasts, employee picker |
| `frontend/src/hooks/` | `useApi` (cancellable GETs), `useList` (filters, debounce, guarded mutations) |
| `frontend/src/lib/` | Navigation and role table, links, formatting, session and feed logic, with `*.test.js` unit tests |
| `package.json`, `package-lock.json` | Scripts (`dev`, `build`, `lint`, `test`, `preview`) and pinned dependencies |
| `vite.config.js`, `.oxlintrc.json`, `index.html` | Build (Tailwind via `@tailwindcss/vite`), lint configuration |
| `.env.example` | Template with an empty `VITE_API_BASE_URL` |
| `README.md` | Frontend documentation (pages, roles, endpoints, behaviour, limitations) |

**Excluded:** `node_modules`, `dist`, caches, and any `.env` (none exists).

To run it: `npm ci`, then `npm run dev` (with the backend on port 8000), or `npm run lint`, `npm test`, `npm run build`.
