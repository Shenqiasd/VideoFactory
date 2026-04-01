# Testing VideoFactory Auth System

## Local Development Setup

1. Start the API server:
   ```bash
   cd /home/ubuntu/repos/VideoFactory
   python -m api.server
   ```
   Server runs on `http://localhost:9000` by default (or `$PORT` if set).

2. The server uses `--reload` via watchfiles, so code changes are picked up automatically for template files. For `auth.py` changes, a server restart may be needed.

## Auth System Architecture

- **User storage**: `config/users.json` (bcrypt hashed passwords)
- **Sessions**: Signed cookies via `itsdangerous` (`vf_session` cookie)
- **Auth template**: `web/templates/auth.html` — single page with client-side tab switching between login/register
- **Auth module**: `api/auth.py` — `registration_allowed()`, `auth_enabled()`, session management
- **Registration policy**: Controlled by `VF_ALLOW_REGISTRATION` env var (defaults to `true`, set to `false` to disable)

## Testing the Auth Page

### Key Routes
- `/login` — Shows auth page with login tab active
- `/register` — Shows auth page with register tab active
- `/api/auth/login` — POST endpoint for login
- `/api/auth/register` — POST endpoint for registration
- `/api/auth/logout` — POST endpoint for logout (NOT GET)
- `/api/auth/status` — GET endpoint returning `{auth_enabled, registration_allowed}`

### Common Testing Gotchas

1. **Form submission**: Browser tool clicks on form submit buttons may be blocked. Use JavaScript to submit:
   ```javascript
   // Fill form fields
   document.querySelector('input[type="text"]').value = 'username';
   document.querySelector('input[type="password"]').value = 'password';
   document.querySelector('input[type="text"]').dispatchEvent(new Event('input'));
   document.querySelector('input[type="password"]').dispatchEvent(new Event('input'));
   // Submit
   document.querySelector('form').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
   ```

2. **Logout is POST-only**: `GET /api/auth/logout` returns 405. Use:
   ```javascript
   fetch('/api/auth/logout', {method: 'POST'}).then(() => window.location.href = '/login');
   ```

3. **Tab switching**: The `switchTab('login')` / `switchTab('register')` functions are globally available in the auth page. If button clicks don't work via browser tool, call them directly via console.

4. **Railway ephemeral filesystem**: On Railway, `config/users.json` is wiped on each redeploy. This means previously registered accounts are lost. Registration defaults to open so users can re-register.

## Test Accounts

Create test accounts locally as needed. There is no default admin account — the first registered user becomes the initial user.

## Devin Secrets Needed

No secrets are required for local auth testing. The session secret is auto-generated and stored at `config/.session_secret`.
