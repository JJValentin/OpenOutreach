#!/usr/bin/env bash
# cdp-smoke.sh
# Playwright-based CDP smoke test for Signal Radar.
# Connects over CDP, explicitly selects LinkedIn tab (not pages[0]),
# checks auth/session state, reports clear errors.
# Exit: 0=smoke pass, 1=smoke fail, 2=setup error

set -euo pipefail

CDP_PORT=9222
HEALTH_CHECK="/home/clawdbot/openoutreach/linkedin/scripts/cdp-health-check.sh"
PYTHON="/home/clawdbot/openoutreach/.venv/bin/python"

log() {
    echo "[smoke] $*"
}

fail() {
    echo "FAIL: $*"
    exit 1
}

setup_error() {
    echo "SETUP_ERROR: $*"
    exit 2
}

# --- 1. Pre-flight health check ---
log "Running pre-flight health check..."
if ! bash "${HEALTH_CHECK}" >/dev/null 2>&1; then
    log "CDP not healthy; attempting recovery..."
    bash /home/clawdbot/openoutreach/linkedin/scripts/cdp-recovery.sh >/dev/null 2>&1 || true
    sleep 3
    if ! bash "${HEALTH_CHECK}" >/dev/null 2>&1; then
        setup_error "CDP unreachable and recovery failed"
    fi
fi
log "CDP healthy"

# --- 2. Run Playwright smoke inline ---
log "Running Playwright CDP smoke..."

"${PYTHON}" << 'PYEOF'
import sys
import requests
from playwright.sync_api import sync_playwright

CDP_ENDPOINT = "http://localhost:9222"
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_SETUP_ERROR = 2

def get_ws_url():
    try:
        resp = requests.get(f"{CDP_ENDPOINT}/json/version", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("webSocketDebuggerUrl")
    except Exception as e:
        print(f"SETUP_ERROR: Cannot reach CDP: {e}", file=sys.stderr)
    return None

ws_url = get_ws_url()
if not ws_url:
    sys.exit(EXIT_SETUP_ERROR)

try:
    playwright = sync_playwright().start()
    browser = playwright.chromium.connect_over_cdp(ws_url)
    context = browser.contexts[0] if browser.contexts else browser.new_context()

    # Explicitly select LinkedIn tab -- do NOT use pages[0]
    page = None
    for p in context.pages:
        if "linkedin.com" in p.url:
            page = p
            break

    if not page:
        # Also check targets via CDP JSON list in case page object not synced
        try:
            targets = requests.get(f"{CDP_ENDPOINT}/json/list", timeout=5).json()
            linkedin_targets = [t for t in targets if "linkedin.com" in t.get("url", "")]
            if linkedin_targets:
                print(f"INFO: LinkedIn target found but not in context.pages; target count={len(linkedin_targets)}")
            else:
                print("FAIL: No LinkedIn tab found among CDP targets", file=sys.stderr)
        except Exception as e:
            print(f"FAIL: No LinkedIn tab found (target list error: {e})", file=sys.stderr)
        sys.exit(EXIT_FAIL)

    print(f"OK: LinkedIn tab selected: {page.url}")

    # Auth check: if URL contains /login, session is likely expired
    if "/login" in page.url:
        print("WARN: LinkedIn shows login page -- session may need re-auth")
        # This is a warning, not a hard fail, because user may still be able to auth manually
        print("RESULT: AUTH_REQUIRED")
        sys.exit(EXIT_FAIL)

    # Light health: can we evaluate JS?
    title = page.evaluate("document.title")
    print(f"OK: Page title = {title}")

    print("RESULT: SMOKE_PASS")
    sys.exit(EXIT_OK)

except Exception as e:
    print(f"FAIL: Playwright smoke error: {e}", file=sys.stderr)
    sys.exit(EXIT_FAIL)
PYEOF
