# Testing Platform Accounts / OAuth Binding UX

## Overview
The platform accounts page (`/platform-accounts`) uses Alpine.js 3 for reactive UI with an OAuth popup flow for connecting social media platforms.

## Server Setup

1. Create a test settings file with fake OAuth credentials:
   ```yaml
   # /tmp/test-settings.yaml
   platforms:
     youtube:
       client_id: "fake-yt-client-id"
       client_secret: "fake-yt-secret"
       redirect_uri: "http://localhost:9000/api/oauth/callback/youtube"
     bilibili:
       client_id: "fake-bili-client-id"
       client_secret: "fake-bili-secret"
       redirect_uri: "http://localhost:9000/api/oauth/callback/bilibili"
   ```
2. Start server: `PYTHONPATH=src VF_CONFIG=/tmp/test-settings.yaml python -m uvicorn api.server:app --host 0.0.0.0 --port 9000`
3. Register test user if needed (delete `users.json` first if stale): POST `/register` with `{"username": "testadmin", "password": "Test1234"}`
4. Login and save cookies: `curl -c /tmp/cookies.txt -X POST http://localhost:9000/api/auth/login -H 'Content-Type: application/json' -d '{"username":"testadmin","password":"Test1234"}'`

## Devin Secrets Needed
No secrets needed — uses fake OAuth credentials for testing.

## Key API Endpoints
- `GET /api/oauth/all-platforms` — Returns all 14 platforms with `configured` boolean
- `POST /api/oauth/connect/{platform}` — Returns `auth_url` and `state` for popup
- `GET /api/oauth/connect-status/{state}` — Polls for OAuth completion
- `GET /api/oauth/accounts` — Lists bound accounts
- `DELETE /api/oauth/accounts/{id}` — Unbinds an account

## Testing with Playwright / Browser Tool

### Alpine.js Interaction
- Playwright's click mechanism may be blocked on Alpine.js buttons inside modals. Workaround: use JavaScript console to interact with Alpine data directly:
  ```js
  const xDataEl = document.querySelector('[x-data]');
  const alpineData = Alpine.$data(xDataEl);
  alpineData.showConnectModal = true; // Open modal
  alpineData.startConnect(alpineData.allPlatforms.find(p => p.platform === 'youtube')); // Start connect
  ```
- Alpine.js Proxy objects may throw `SecurityError` when accessed from Playwright's evaluate context. The function may still execute despite the error.

### Popup Limitations
- `window.open()` is blocked by Playwright's default popup policy. The first connect attempt may succeed but subsequent ones may show "发起授权失败" (authorization initiation failed). This is a test environment limitation, not a code bug.
- To fully test the OAuth popup flow end-to-end, you would need real OAuth credentials and a browser that allows popups.

### Timer Leak Verification
- Start a connect flow, note the countdown time (e.g., 5:00)
- Wait a known number of seconds (e.g., 5s)
- Check countdown again — it should have decreased by exactly that many seconds
- If it decreased by 2x, there's a timer leak (multiple intervals running)

## Testing Bound Accounts
To test account display without real OAuth, insert directly into SQLite:
```python
import sqlite3, uuid, datetime
conn = sqlite3.connect('data/video_factory.db')
account_id = str(uuid.uuid4())[:8]
now = datetime.datetime.now().isoformat()
conn.execute('''INSERT INTO platform_accounts 
  (id, user_id, platform, auth_method, platform_uid, username, nickname, avatar_url, status, cookie_path, last_login_at, created_at, updated_at)
  VALUES (?, '', 'youtube', 'oauth2', 'UC_test123', 'TestUser', 'Test Channel', '', 'active', '', ?, ?, ?)''',
  (account_id, now, now, now))
conn.commit()
```

## Old Routes Verification
- `GET /accounts` should return 404 (old page removed)
- `GET /api/publish/accounts` should return 404 (old API removed)
