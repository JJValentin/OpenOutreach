# CDP On-Demand Runbook

## Overview

Signal Radar uses Chrome DevTools Protocol (CDP) on port 9222 to interact with LinkedIn. Chrome is no longer kept running 24/7. Instead, it starts on-demand via recovery/launcher scripts and can be stopped when idle.

## Why On-Demand?

- **Memory savings**: Chrome consumes ~1.5GB RAM when idle. On-demand means zero idle cost.
- **Stuck-state recovery**: CDP targets can leak or get stuck (ghost DevTools pages). Recovery scripts cleanly restart only Chrome.
- **No daemon restart needed**: OpenOutreach `rundaemon` is NOT restarted by CDP operations.

## Scripts

All scripts live in `/home/clawdbot/openoutreach/linkedin/scripts/`.

### `cdp-health-check.sh [--verbose]`

Verifies CDP is healthy before any operation.

**Checks:**
- Port 9222 reachable
- Process on port is actually Chrome
- At least one LinkedIn tab exists among CDP targets
- Warns if ghost targets (chrome://inspect, about:blank) dominate

**Exit codes:**
- `0` — CDP healthy
- `1` — CDP unhealthy
- `2` — Usage error

**Usage:**
```bash
bash /home/clawdbot/openoutreach/linkedin/scripts/cdp-health-check.sh --verbose
```

### `cdp-recovery.sh`

Recovers from stuck or missing CDP. Idempotent — if CDP is already healthy, exits immediately.

**What it does:**
1. Checks health; skips if already healthy
2. Narrow-kills ONLY the Chrome process on port 9222 (via `lsof`)
3. Preserves `--user-data-dir` (`/home/clawdbot/.chrome-linkedin-profile/`)
4. Launches Chrome with CDP bound to localhost only (`--remote-debugging-address=127.0.0.1`)
5. Waits up to 30s for CDP to respond
6. Runs health check to confirm recovery

**Safety:**
- Never restarts `openoutreach-rundaemon`
- Never uses `killall chrome`
- Binds CDP to localhost only

**Exit codes:**
- `0` — Recovered and healthy (or already healthy)
- `1` — Recovery failed

**Usage:**
```bash
bash /home/clawdbot/openoutreach/linkedin/scripts/cdp-recovery.sh
```

### `cdp-smoke.sh`

Playwright-based integration smoke test.

**What it does:**
1. Runs health check (auto-recovers if needed)
2. Connects to Chrome via CDP WebSocket
3. Explicitly selects the LinkedIn tab (avoids ghost DevTools page issue)
4. Checks if LinkedIn shows a login page (auth-required warning)
5. Evaluates page JS to confirm CDP session is functional

**Exit codes:**
- `0` — Smoke pass
- `1` — Smoke fail (no LinkedIn tab, auth required, or JS eval failed)
- `2` — Setup error (CDP unreachable)

**Usage:**
```bash
bash /home/clawdbot/openoutreach/linkedin/scripts/cdp-smoke.sh
```

## Operational Workflows

### Before a Signal Radar polling run

```bash
bash /home/clawdbot/openoutreach/linkedin/scripts/cdp-health-check.sh || \
  bash /home/clawdbot/openoutreach/linkedin/scripts/cdp-recovery.sh
```

### If smoke test shows AUTH_REQUIRED

The LinkedIn session cookie in the Chrome profile has expired. Options:
1. Manually open Chrome/VNC, log in to LinkedIn, close browser.
2. Or use the existing authenticated Chrome session if available.

The profile at `/home/clawdbot/.chrome-linkedin-profile/` is preserved across Chrome restarts.

### Stopping Chrome after operations

If you want zero idle Chrome after Signal Radar is done:

```bash
# Narrow kill (same as recovery step 2)
lsof -ti:9222 | xargs kill -TERM
```

Or simply leave Chrome running — the next `cdp-recovery.sh` will reuse it if healthy.

## Service Retirement

The old `linkedin-chrome.service` systemd user service has been **disabled and masked**. It is no longer started at boot or restarted automatically.

The `openoutreach-rundaemon.service` no longer depends on `linkedin-chrome.service`.

## Files and Paths

| Path | Purpose |
|------|---------|
| `/home/clawdbot/openoutreach/linkedin/scripts/cdp-health-check.sh` | Health verification |
| `/home/clawdbot/openoutreach/linkedin/scripts/cdp-recovery.sh` | On-demand launch/recovery |
| `/home/clawdbot/openoutreach/linkedin/scripts/cdp-smoke.sh` | Playwright smoke test |
| `/home/clawdbot/.chrome-linkedin-profile/` | Chrome user data (sessions, cookies) |
| `/tmp/linkedin-chrome-nohup.log` | Chrome stdout/stderr from recovery launches |
| `/tmp/linkedin-chrome.pid` | PID file from last recovery launch |

## Troubleshooting

| Symptom | Action |
|---------|--------|
| CDP unreachable | Run `cdp-recovery.sh` |
| Health check shows no LinkedIn tab | Run `cdp-recovery.sh` — Chrome may have opened to wrong page |
| Smoke test says AUTH_REQUIRED | Re-authenticate LinkedIn in Chrome; profile is preserved |
| Ghost targets dominate | Run `cdp-recovery.sh` to restart Chrome cleanly |
| Recovery fails (port still occupied) | Check `lsof -ti:9222` and manually kill, then retry |

## Safety Checklist

- [ ] `openoutreach-rundaemon` is NOT restarted by any CDP script
- [ ] CDP is bound to `127.0.0.1:9222` only
- [ ] Chrome profile directory is never deleted by scripts
- [ ] Only the Chrome process on port 9222 is killed (not all Chrome processes)
