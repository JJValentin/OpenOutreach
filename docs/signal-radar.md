# Signal Radar — Operator Runbook

## 1. Overview

Signal Radar discovers high-intent LinkedIn engagement signals and injects them as warm leads into the outreach pipeline.

**Three source kinds:**

| Kind | Description |
|------|-------------|
| OWN_PROFILE | Your own company's LinkedIn posts |
| COMPETITOR_COMPANY | A competitor's LinkedIn company page |
| INFLUENCER_PROFILE | A third-party influencer's LinkedIn personal profile |

**Scoring flow:** Engagement events (reactions, comments, reposts) are fetched from Voyager API, scored against base × recency × frequency, then injected as Deals when score ≥ SIGNAL_PRIORITY_THRESHOLD (default 50).

---

## 2. Enabling

1. Edit openoutreach/settings.py (or your override file):
   ```python
   SIGNAL_RADAR_ENABLED = True
   ```
2. Restart the daemon worker (Celery or systemd service):
   ```bash
   sudo systemctl restart openoutreach-worker
   ```
   Or restart the Celery process if using Celery directly.

---

## 3. Adding Sources

**Admin path:** /admin/linkedin/watchedsource/

**URL formats accepted:**

| Kind | Format | Example |
|------|--------|---------|
| OWN_PROFILE | Personal profile URL or urn | https://www.linkedin.com/in/yourcompany |
| COMPETITOR_COMPANY | Company page URL or urn | https://www.linkedin.com/company/acme |
| INFLUENCER_PROFILE | Personal profile URL or urn | https://www.linkedin.com/in/influencer |

**Per-campaign cap:** Maximum 20 sources per campaign. The admin form enforces this.

---

## 4. Cadence Guidance

| Source Kind | Recommended Cadence |
|-------------|---------------------|
| OWN_PROFILE | 30 minutes |
| COMPETITOR_COMPANY | 1–4 hours |
| INFLUENCER_PROFILE | 1–4 hours |

**Why jitter exists:** Each poll is scheduled at cadence ± 25% random offset to avoid thundering-herd patterns when multiple sources have the same cadence.

---

## 5. Scoring Table

**Base scores by engagement type × source kind:**

| Engagement Type | OWN_PROFILE | COMPETITOR_COMPANY | INFLUENCER_PROFILE |
|----------------|-------------|-------------------|-------------------|
| Comment | 100 | 75 | 75 |
| Reaction | 85 | 55 | 55 |
| Repost | 70 | 70 | 70 |

**Recency tiers:**

| Event Age | Multiplier |
|-----------|-----------|
| ≤ 3 days | 1.0 |
| ≤ 10 days | 0.75 |
| ≤ 25 days | 0.5 |
| > 25 days | 0.2 |

**Frequency bonus:** Number of signals from same profile in past 30 days:

| Signal Count | Bonus |
|--------------|-------|
| 1 | 0 |
| 2 | +10 |
| 3 | +15 |
| ≥ 4 | +20 |

**Composite score cap:** 150

**Formula:** min(150, sum(base × recency) + frequency_bonus)

---

## 6. Threshold Tuning

SIGNAL_PRIORITY_THRESHOLD (default: 50)

- **Raise** to reduce false-positive injections (fewer, higher-quality leads)
- **Lower** to widen the funnel (more leads, more noise)

Change in settings.py and restart the worker.

---

## 7. Disabling a Bad Source

1. Go to /admin/linkedin/watchedsource/
2. Select the source checkbox
3. Choose **Disable selected sources** from the action dropdown
4. Click **Go**

**Auto-disable:** After 3 consecutive polling failures, a source is automatically disabled and its last_error is recorded.

---

## 8. Rate-Limit Pause

When a 429 response is received from LinkedIn API, Signal Radar enters a **global 4-hour pause**:

- All polling tasks are suspended for 4 hours
- A SignalRadarState record is created/updated with paused_until = now + 4h
- Manual override via admin:
  - **Pause Polling Globally:** Sets pause to now + SIGNAL_RATE_LIMIT_PAUSE_HOURS
  - **Resume Polling Globally:** Clears paused_until immediately

Admin path: /admin/linkedin/signalradarstate/

---

## 9. Operational Anti-Patterns

| Anti-pattern | Problem | Fix |
|--------------|---------|-----|
| Too many sources | API rate limits hit faster; noise increases | Cap at 20 per campaign |
| Too-low cadence (< 30 min) | LinkedIn throttles; 429 spikes | Use 30 min minimum for OWN_PROFILE |
| Mixing campaigns into one source | Wrong attribution; cross-campaign pollution | Each campaign gets its own sources |
| Removing campaign denormalization | watched_source.campaign link is intentional | Never decouple sources from campaigns |

---

## 10. Future Work (Deferred from MVP)

The following are out of MVP scope and deferred to future releases:

- **2.1–2.4: Full HTTP API endpoints** for watched source CRUD operations. Admin actions at /admin/linkedin/watchedsource/ are sufficient for MVP.
- **5.6: Approval queue** for injected signal profiles. Profiles are injected directly without review in MVP.

---

## Quick Reference

| Item | Value |
|------|-------|
| Admin URL | /admin/linkedin/watchedsource/ |
| Global pause admin | /admin/linkedin/signalradarstate/ |
| Default threshold | 50 |
| Score cap | 150 |
| Auto-disable | After 3 consecutive failures |
| Rate-limit pause | 4 hours |
## Endpoint verification status

The Voyager API endpoint paths in `linkedin/api/posts.py` are best-effort
based on patterns from existing OpenOutreach calls and community reverse
engineering. They are NOT verified. Status as of 2026-04-30:

| Wrapper | Confidence | Status | Probe Result |
|---|---|---|---|
| `list_own_profile_posts` | UNCERTAIN | verified | HTTP 400 (empty list, path OK) |
| `list_profile_posts` | LIKELY | failed | HTTP 400 - needs DevTools |
| `list_company_posts` | UNCERTAIN | failed | HTTP 400 - needs DevTools |
| `list_post_reactors` | GUESS | failed | no first_post_urn - needs posts first |
| `list_post_comments` | GUESS | failed | no first_post_urn - needs posts first |
| `list_post_reposts` | GUESS | failed | no first_post_urn - needs posts first |

A first server-side probe (2026-04-30, MindPalace) produced one verification
and five failures (see table above). The probe script initially crashed on
Django import — fixed with proper django.setup() boilerplate. Probe used
JJValentin's authenticated cookie session. Cookies were exported from the
operator's browser, written to MindPalace at chmod 600, then fully deleted
afterward (no residue, confirmed by grep). `list_own_profile_posts` returned
HTTP 400 with an empty list — the endpoint path /feed/updates is structurally
correct but the account has no posts. Other wrappers failed as documented above.

### Recommended next probe: operator-local

Run the probe from the operator's own workstation, not from MindPalace:

1. Clone the fork:
   `git clone https://github.com/JJValentin/OpenOutreach.git`
2. Check out: `git checkout feature/signal-radar`
3. Set up venv with Python 3.12 and `requirements/base.txt + local.txt`.
4. `playwright install chromium` (no `--with-deps` needed on a normal
   workstation that already has Chrome libraries).
5. Use the existing OpenOutreach onboarding flow OR construct a minimal
   `LinkedInProfile` pre-populated with cookies exported from your
   logged-in browser (Playwright `storage_state` JSON format).
6. Open `manage.py shell` and call each wrapper one-by-one with
   ~30s gaps between. Capture URL, status, response shape per wrapper.
7. Update `linkedin/api/posts.py`: remove `# TODO: verify endpoint path`
   markers from verified wrappers; add `EndpointNotVerified` raises to
   wrappers that returned 4xx/5xx; commit; push.
8. If any wrapper still 404s, open one of your own LinkedIn posts in a
   browser, open DevTools → Network → filter `voyager`, click
   "see who reacted / commented / reposted", and capture the actual
   request URL. Update the wrapper accordingly. Re-probe.

### Why local instead of server?

- Your browser already has the authenticated session — no cookie export
  to a remote box.
- Your normal IP avoids "unusual login location" flags.
- You can run headed Playwright and observe what LinkedIn returns.
- No SSH friction; iterative debugging is faster.

## Rate-Limit Pause Behavior

Signal Radar implements an automatic rate-limit pause to back off when LinkedIn returns 429 responses.

### Mechanics
- **Scope:** GLOBAL. The pause applies to ALL watched sources, not per-source. Implemented as a singleton SignalRadarState model.
- **Trigger:** Automatic. When any poll returns HTTP 429, SignalRadarState.paused_until is set to now + SIGNAL_RATE_LIMIT_PAUSE_HOURS hours (default 4).
- **Auto-resume:** Yes. The pause expires when paused_until datetime passes; no explicit resume task is required. Subsequent polls will check is_signal_radar_paused() and resume normally.

### Per-source failures (separate from global pause)
- Each WatchedSource tracks consecutive_failures independently.
- After MAX_CONSECUTIVE_FAILURES (default 3) consecutive errors on a single source, that source is auto-disabled (is_active=False) — distinct from the global pause.

### Visibility (Django admin)
- Navigate to **Signal Radar State** in the admin (singleton). The paused_until field shows the exact resume time when active.
- The changelist surfaces a banner if any unhealthy sources are present (consecutive_failures >= 1).

### Manual resume
- Admin -> Signal Radar State -> action **Resume Signal Radar globally** clears paused_until immediately.
- Admin -> Signal Radar State -> action **Pause Signal Radar globally for 4 hours** sets it manually.

### Configuration
- SIGNAL_RATE_LIMIT_PAUSE_HOURS = 4 (in linkedin/conf.py) — default pause duration on 429.

---

## Operational Status (Change B)

### What's NOT Working

| Feature | Status | Change |
|---------|--------|--------|
| Polling daemon | Not running — systemd unit not installed | Change A (systemd units) |
| fetchProfilePosts | Stub — not implemented | Change C (fetchProfilePosts) |
| fetchCompanyPosts | Endpoint unverified — returns empty results | No confirmed fix yet |
| fetchPostReactions | Endpoint unverified | No confirmed fix yet |
| fetchPostComments | Endpoint unverified | No confirmed fix yet |
| fetchPostReposts | Endpoint unverified | No confirmed fix yet |

All engagement operations have been implemented in code but endpoint paths have not been
verified against a live LinkedIn session. See "Endpoint verification status" section above.

---

## How to Verify the Polling Daemon

The Signal Radar polling daemon runs as a user-mode systemd service: `openoutreach-rundaemon.service`.

### Check installation
The unit file is installed by the operational hardening change's setup script. Verify presence:
```bash
systemctl --user list-unit-files openoutreach-rundaemon.service
```

### Check active state
```bash
systemctl --user is-active openoutreach-rundaemon.service
```
Expected outcomes:
- `active` — daemon is running and polling on schedule
- `inactive` — daemon is installed but not started (default until DEPLOY phase activates it)
- `failed` — daemon crashed; check `journalctl --user -u openoutreach-rundaemon -n 100`

### Activation
The daemon is intentionally NOT enabled on initial install when `WatchedSource` records exist. Activation happens during DEPLOY phase after operator confirmation:
```bash
systemctl --user enable --now openoutreach-rundaemon.service
```

### Boot survival
For the daemon to survive logout/reboot, the user must have linger enabled (one-time setup, requires sudo):
```bash
sudo loginctl enable-linger clawdbot
```
Confirm with: `loginctl show-user clawdbot | grep Linger` (should show `Linger=yes`).

**Run the smoke test:**
```bash
cd /home/clawdbot/openoutreach
python manage.py smoke_test_signal_radar --target-company 1337
```

Expected output when LinkedIn operations work:
```
Signal Radar Smoke Test
Target company: 1337
Chrome CDP: ws://localhost:9222/devtools/browser/...
Profile: your-username
---
  [PASS] fetchCompanyPosts: N items
  [PASS] fetchPostReactions: N items
  [PASS] fetchPostComments: N items
  [PASS] fetchPostReposts: N items
---
Overall: PASS (exit 0)
```

Note: Zero items per operation is a PASS (valid — LinkedIn company 1337 may have no recent engagement). If the target company has no recent posts, engagement ops (reactions/comments/reposts) will be marked SKIP. Use `--fallback-post-urn <urn>` to exercise those ops against a known post.

**Exit codes:**
| Code | Meaning |
|------|---------|
| 0 | All operations passed (at least one PASS, no FAILs) |
| 1 | At least one operation failed, OR all ops skipped (insufficient signal) |
| 2 | Setup error (no Chrome, no active LinkedInProfile) |

**JSON output (for monitoring):**
```bash
python manage.py smoke_test_signal_radar --target-company 1337 --json
```
Output: `{"overall_pass": true, "exit_code": 0, "operations": {...}, "profile": "username"}`

Each operation in the JSON output includes a `status` field: `"PASS"`, `"FAIL"`, or `"SKIP"`.

---

## How to Read Poll Health from Admin

After applying the database migration (`python manage.py migrate linkedin`), poll health fields
are visible in the Django admin.

**WatchedSource admin:** `/admin/linkedin/watchedsource/`
- **Last successful poll at** column: shows the datetime of the last successful poll per source, or blank if never polled
- **Status** filter (sidebar): filters by Healthy / Stale / Failing
  - **Healthy**: zero consecutive failures AND last successful poll within 2× cadence
  - **Stale**: zero consecutive failures but no recent poll (source is overdue)
  - **Failing**: one or more consecutive failures

**SignalRadarState admin:** `/admin/linkedin/signalradarstate/`
- **Paused Status** field: shows "Currently paused: yes (until <datetime>)" or "Currently paused: no"
- **Countdown** field: shows "2h 14m remaining" if paused

To manually clear a rate-limit pause: use the "Resume Signal Radar globally" action in the SignalRadarState admin.
