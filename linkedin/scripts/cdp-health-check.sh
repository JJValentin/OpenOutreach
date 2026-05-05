#!/usr/bin/env bash
# cdp-health-check.sh
# Verifies Chrome CDP health for Signal Radar.
# Checks: port reachability, Chrome process identity, LinkedIn tab presence, ghost targets.
# Exit: 0=healthy, 1=unhealthy, 2=usage error

set -euo pipefail

CDP_PORT=9222
CDP_URL="http://localhost:${CDP_PORT}"
USER_DATA_DIR="/home/clawdbot/.chrome-linkedin-profile"
TIMEOUT=5

usage() {
    echo "Usage: $0 [--verbose]"
    exit 2
}

VERBOSE=0
if [[ "${1:-}" == "--verbose" ]]; then
    VERBOSE=1
fi

log() {
    if [[ $VERBOSE -eq 1 ]]; then
        echo "[health-check] $*"
    fi
}

fail() {
    echo "FAIL: $*"
    exit 1
}

# 1. Port reachability
log "Checking port ${CDP_PORT}..."
if ! curl -sf --max-time "${TIMEOUT}" "${CDP_URL}/json/version" > /dev/null 2>&1; then
    fail "CDP port ${CDP_PORT} not reachable"
fi
log "Port ${CDP_PORT} reachable"

# 2. Verify process on port is actually Chrome
log "Verifying Chrome process on port ${CDP_PORT}..."
PID_ON_PORT=$(lsof -ti:${CDP_PORT} 2>/dev/null || true)
if [[ -z "${PID_ON_PORT}" ]]; then
    fail "No process found on port ${CDP_PORT}"
fi

# Read cmdline safely (null-separated)
PROC_CMDLINE=""
if [[ -r /proc/${PID_ON_PORT}/cmdline ]]; then
    PROC_CMDLINE=$(python3 -c "import sys; print(open('/proc/${PID_ON_PORT}/cmdline','rb').read().replace(b'\\x00',b' ').decode('utf-8','replace'))" 2>/dev/null || true)
fi

IS_CHROME=0
if [[ -n "${PROC_CMDLINE}" ]]; then
    if [[ "${PROC_CMDLINE}" =~ google-chrome ]] || [[ "${PROC_CMDLINE}" =~ /chrome[[:space:]] ]]; then
        IS_CHROME=1
    fi
fi

if [[ ${IS_CHROME} -eq 0 ]]; then
    PS_CMDLINE=$(ps -p "${PID_ON_PORT}" -o cmd= 2>/dev/null || true)
    if [[ -n "${PS_CMDLINE}" ]]; then
        if [[ "${PS_CMDLINE}" =~ google-chrome ]] || [[ "${PS_CMDLINE}" =~ /chrome[[:space:]] ]]; then
            IS_CHROME=1
        fi
    fi
fi

if [[ ${IS_CHROME} -eq 0 ]]; then
    fail "Process on port ${CDP_PORT} is not Chrome (PID ${PID_ON_PORT})"
fi
log "Chrome confirmed on PID ${PID_ON_PORT}"

# 3. Check for LinkedIn tab (not ghost DevTools)
log "Checking LinkedIn tab..."
TARGETS_JSON=$(curl -sf --max-time "${TIMEOUT}" "${CDP_URL}/json/list" 2>/dev/null || true)
if [[ -z "${TARGETS_JSON}" ]]; then
    fail "Cannot fetch CDP target list"
fi

LINKEDIN_COUNT=$(echo "${TARGETS_JSON}" | python3 -c "import sys,json; data=json.load(sys.stdin); print(sum(1 for t in data if 'linkedin.com' in t.get('url','')))" 2>/dev/null || echo 0)
GHOST_COUNT=$(echo "${TARGETS_JSON}" | python3 -c "import sys,json; data=json.load(sys.stdin); print(sum(1 for t in data if any(x in t.get('url','') for x in ['chrome://inspect','chrome://newtab','about:blank','chrome-devtools'])))" 2>/dev/null || echo 0)
TOTAL_TARGETS=$(echo "${TARGETS_JSON}" | python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo 0)

log "Targets: total=${TOTAL_TARGETS}, linkedin=${LINKEDIN_COUNT}, ghost=${GHOST_COUNT}"

if [[ "${LINKEDIN_COUNT}" -eq 0 ]]; then
    fail "No LinkedIn tab found among ${TOTAL_TARGETS} targets"
fi

if [[ "${TOTAL_TARGETS}" -gt 5 ]]; then
    echo "WARN: High target count (${TOTAL_TARGETS}) -- possible leak"
fi

echo "OK: CDP healthy (Chrome PID ${PID_ON_PORT}, ${LINKEDIN_COUNT} LinkedIn tab(s), ${TOTAL_TARGETS} total targets)"
exit 0
