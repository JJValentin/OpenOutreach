#!/usr/bin/env bash
# cdp-recovery.sh
# On-demand Chrome CDP recovery. Narrow-kills only the Chrome process on CDP port,
# preserves user-data-dir, relaunches Chrome, verifies health.
# Never restarts openoutreach-rundaemon.
# Exit: 0=recovered and healthy, 1=recovery failed

set -euo pipefail

CDP_PORT=9222
USER_DATA_DIR="/home/clawdbot/.chrome-linkedin-profile"
CHROME_BIN="/usr/bin/google-chrome-stable"
DISPLAY_ENV="${DISPLAY:-:99}"
HEALTH_CHECK="/home/clawdbot/openoutreach/linkedin/scripts/cdp-health-check.sh"
LAUNCH_TIMEOUT=30

log() {
    echo "[recovery] $*"
}

fail() {
    echo "FAIL: $*"
    exit 1
}

# --- 1. Check if already healthy ---
log "Checking current CDP health..."
if bash "${HEALTH_CHECK}" >/dev/null 2>&1; then
    log "CDP already healthy; nothing to do"
    exit 0
fi
log "CDP unhealthy; proceeding with recovery"

# --- 2. Narrow kill of Chrome on CDP port ---
log "Finding Chrome process on port ${CDP_PORT}..."
PID_ON_PORT=$(lsof -ti:${CDP_PORT} 2>/dev/null || true)
if [[ -n "${PID_ON_PORT}" ]]; then
    log "Killing Chrome PID(s): ${PID_ON_PORT}"
    echo "${PID_ON_PORT}" | xargs kill -TERM 2>/dev/null || true
    sleep 2
    # Force kill if still alive
    REMAINING=$(lsof -ti:${CDP_PORT} 2>/dev/null || true)
    if [[ -n "${REMAINING}" ]]; then
        log "Force-killing remaining PID(s): ${REMAINING}"
        echo "${REMAINING}" | xargs kill -KILL 2>/dev/null || true
        sleep 1
    fi
else
    log "No process found on port ${CDP_PORT}"
fi

# --- 3. Verify port is free ---
if lsof -ti:${CDP_PORT} >/dev/null 2>&1; then
    fail "Port ${CDP_PORT} still occupied after kill"
fi
log "Port ${CDP_PORT} is free"

# --- 4. Ensure user-data-dir exists ---
if [[ ! -d "${USER_DATA_DIR}" ]]; then
    log "Creating user-data-dir: ${USER_DATA_DIR}"
    mkdir -p "${USER_DATA_DIR}"
fi

# --- 5. Launch Chrome with CDP ---
log "Launching Chrome (DISPLAY=${DISPLAY_ENV})..."
export DISPLAY="${DISPLAY_ENV}"
nohup "${CHROME_BIN}" \
    --remote-debugging-port=${CDP_PORT} \
    --remote-debugging-address=127.0.0.1 \
    --no-first-run \
    --no-default-browser-check \
    --ozone-platform=x11 \
    --disable-gpu \
    --window-size=1280,800 \
    --user-data-dir="${USER_DATA_DIR}" \
    "https://www.linkedin.com/login" \
    > /tmp/linkedin-chrome-nohup.log 2>&1 &
echo $! > /tmp/linkedin-chrome.pid
log "Chrome launched (PID $(cat /tmp/linkedin-chrome.pid))"

# --- 6. Wait for CDP to come up ---
log "Waiting up to ${LAUNCH_TIMEOUT}s for CDP..."
for i in $(seq 1 "${LAUNCH_TIMEOUT}"); do
    if curl -sf --max-time 2 "http://localhost:${CDP_PORT}/json/version" >/dev/null 2>&1; then
        log "CDP responding after ${i}s"
        break
    fi
    sleep 1
done

if ! curl -sf --max-time 2 "http://localhost:${CDP_PORT}/json/version" >/dev/null 2>&1; then
    fail "CDP did not come up within ${LAUNCH_TIMEOUT}s"
fi

# --- 7. Final health check ---
log "Running health check..."
if bash "${HEALTH_CHECK}" >/dev/null 2>&1; then
    log "Recovery complete -- CDP is healthy"
    echo "OK: CDP recovered and healthy"
    exit 0
else
    fail "Recovery launched Chrome but health check still fails"
fi
