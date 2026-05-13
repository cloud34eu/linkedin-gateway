# Chrome Extension Security Audit

**Date:** 2026-05-13  
**Scope:** `chrome-extension/src-v2/` — all JS, JSON, HTML, and config files  
**Audited by:** Claude Code (claude-sonnet-4-6)

---

## Summary

No third-party spyware, analytics, or telemetry was found. All network traffic originates from:
- The gateway backend (`ainnovate.tech` by default, patched to `localhost`)
- LinkedIn's own Voyager/GraphQL API (`www.linkedin.com`)
- Google OAuth endpoints (`accounts.google.com`, `oauth2.googleapis.com`)

The risks found are **architectural by design** (the extension relays LinkedIn session data to whoever operates the backend) and one **embedded third-party OAuth credential** of medium concern.

---

## 1. Hardcoded Domain References — Full Inventory

### 1.1 `ainnovate.tech` (Gateway Backend)

All patched to `localhost:7778` as part of local setup.

| File | Line(s) | Original Value | Status |
|---|---|---|---|
| `src-v2/shared/config/app.config.js` | 26 | `https://lg.ainnovate.tech` | **Patched → localhost** |
| `src-v2/background/services/storage.service.js` | 79, 81 | `https://lg.ainnovate.tech` | **Patched → localhost** |
| `src-v2/background/controllers/gemini.controller.js` | 50, 51 | `https://lg.ainnovate.tech` | **Patched → localhost** |
| `src-v2/pages/components/dashboard/ServerSettings.js` | 15 | `https://lg.ainnovate.tech` | **Patched → localhost** |
| `src-v2/pages/dashboard/index.js` | 219 | `https://ainnovate.tech/lg-api-docs/` | Docs link only — opens in new tab, no data sent |
| `webpack.config.v2.js` | 53–54 | `https://lgdev.ainnovate.tech` | **Patched → localhost** (dev build) |
| `webpack.config.prod.v2.js` | 53–54 | `https://lgdev.ainnovate.tech` | Not patched — production build, not used locally |
| `manifest.v2.json` | 33–34 | `https://lg.ainnovate.tech/*`, `https://lgdev.ainnovate.tech/*` | Host permissions — harmless for local use |
| `src-v2/shared/config/api.config.js` | 27 | `https://lgprod.ainnovate.tech` | Inside a comment only — not active code |

### 1.2 Google / Gemini OAuth Endpoints (Expected)

| File | Value | Purpose |
|---|---|---|
| `src-v2/shared/constants/gemini-constants.js` | `https://accounts.google.com/o/oauth2/v2/auth` | Google OAuth authorization |
| `src-v2/shared/constants/gemini-constants.js` | `https://oauth2.googleapis.com/token` | Google token exchange |

### 1.3 LinkedIn Voyager API (Expected)

| File | Value | Purpose |
|---|---|---|
| `src-v2/background/controllers/linkedin.controller.js` | `https://www.linkedin.com/voyager/api/...` | Login status check |
| `src-v2/content/linkedin/feed.js` | `https://www.linkedin.com/voyager/api/feed/...` | Feed fetch |
| `src-v2/content/linkedin/posts.js` | `https://www.linkedin.com/voyager/api/graphql` | Posts GraphQL |
| `src-v2/content/linkedin/comments.js` | `https://www.linkedin.com/voyager/api/graphql` | Comments GraphQL |

---

## 2. Embedded Credentials

### 2.1 Google OAuth Client Secret — Medium Risk

**File:** `src-v2/shared/constants/gemini-constants.js` (lines 23, 31)

```
GEMINI_CLIENT_ID:     681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com
GEMINI_CLIENT_SECRET: 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
```

**Context:** These are intentionally public credentials borrowed from the open-source project [`gzzhongqi/geminicli2api`](https://github.com/gzzhongqi/geminicli2api). The code includes a `// gitleaks:allow` comment to suppress secret scanners.

**Why it still matters:**
- These are someone else's Google Cloud project credentials, not yours
- If Google revokes them (due to abuse by any user of that OSS project), the Gemini OAuth flow breaks for everyone
- Anyone who knows these credentials can impersonate the same OAuth app

**Used in:** `gemini.controller.js` — only when the user connects their Google/Gemini account. Not active unless Gemini feature is used.

**Recommendation:** Create your own Google Cloud OAuth app and replace these credentials if you use the Gemini feature in production.

---

## 3. Data Sent to the Backend Server

This is the core of the proxy architecture. The following user data is transmitted to whichever backend the extension is connected to:

| Data | When Sent | Code Location |
|---|---|---|
| LinkedIn CSRF token (`JSESSIONID`) | On login, on API key creation, on every WebSocket reconnect | `auth.service.js`, `linkedin.controller.js`, `websocket.service.js` |
| All LinkedIn session cookies | Same cadence as CSRF token | `linkedin.controller.js:255`, `auth.service.js:226` |
| Google OAuth access + refresh tokens | When user connects Gemini | `gemini.controller.js:514` |
| Browser metadata (name, version, OS, platform) | On install/update/startup | `instance.service.js` |
| LinkedIn API responses | On every `REQUEST_PROXY_HTTP` command from the backend | `websocket.service.js:811` |

**Since you self-host the backend, you control all of this data.**

---

## 4. The Proxy Architecture — Highest Architectural Risk

**File:** `src-v2/background/services/websocket.service.js` (lines 758–876)

The extension implements a remote HTTP proxy over WebSocket:

1. Extension connects to `ws://localhost:7778/ws` with a stable `instance_id`
2. The backend can send a `REQUEST_PROXY_HTTP` message
3. The extension executes **any HTTP request specified by the backend** with `credentials: 'include'` (LinkedIn session cookies auto-attached)
4. The response is sent back to the backend

A companion message `REQUEST_REFRESH_LINKEDIN_SESSION` lets the backend pull a fresh copy of all LinkedIn cookies at any time.

**This is the intended design** — it is literally what the gateway does. The risk is entirely about who operates the backend. Self-hosted = you control it.

---

## 5. Manifest Permissions

**File:** `manifest.v2.json`

```json
"optional_host_permissions": [
  "https://*/*",
  "http://*/*"
]
```

These are *optional* — the user must explicitly grant them. But if granted, combined with the `REQUEST_PROXY_HTTP` handler, the backend could instruct the extension to proxy requests with cookies to **any domain**, not just LinkedIn.

Also: `manifest.json` (the non-v2 one) contains **invalid JSON** — a missing closing `]`. This file appears to be an abandoned artifact and is not used by the build.

---

## 6. No Third-Party Tracking Found

The following services were explicitly searched for and **not found**:
- Google Analytics / Tag Manager
- Mixpanel, Amplitude, Segment
- Sentry, Bugsnag, Datadog
- Hotjar, FullStory
- Any pixel or beacon endpoints

---

## 7. Risk Summary

| Finding | Risk Level | Notes |
|---|---|---|
| Backend relays all LinkedIn cookies | High (by design) | Self-hosted — you control it |
| `REQUEST_PROXY_HTTP` — arbitrary LinkedIn API calls | High (by design) | Core feature |
| `optional_host_permissions: */*` | Medium | User must explicitly grant; not auto-granted |
| Embedded third-party Google OAuth credentials | Medium | Only relevant if Gemini feature is used |
| `ainnovate.tech` hardcoded fallbacks | Low | All patched to localhost |
| `manifest.json` broken JSON | Low | Unused artifact |
| `webpack.config.prod.v2.js` bakes `lgdev` URL | Low | Production build not used locally |
| No third-party analytics or tracking | None (clean) | — |
