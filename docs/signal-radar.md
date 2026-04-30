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
   `python
   SIGNAL_RADAR_ENABLED = True
   `
2. Restart the daemon worker (Celery or systemd service):
   `ash
   sudo systemctl restart openoutreach-worker
   `
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
  - **Pause Polling Globally:** Sets pause to 
ow + SIGNAL_RATE_LIMIT_PAUSE_HOURS
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

| Wrapper | Confidence | Status |
|---|---|---|
| `list_own_profile_posts` | UNCERTAIN | unverified |
| `list_profile_posts` | LIKELY | unverified |
| `list_company_posts` | UNCERTAIN | unverified |
| `list_post_reactors` | GUESS | unverified |
| `list_post_comments` | GUESS | unverified |
| `list_post_reposts` | GUESS | unverified |

A first server-side probe attempt (2026-04-30, MindPalace) produced no
verifications: the probe script crashed on Django module import before
any browser or LinkedIn API call. Cookies were exported from the
operator's browser, written to MindPalace at chmod 600, then fully
deleted afterward (no residue, confirmed by grep).

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