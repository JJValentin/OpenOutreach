#!/usr/bin/env bash
# setup-signal-radar-systemd.sh
# Idempotent setup script for Signal Radar systemd services.
# Run as clawdbot user on MindPalace.
#
# NOTE: linkedin-chrome.service is RETIRED. Chrome runs on-demand via
#       linkedin/scripts/cdp-recovery.sh. Do NOT re-enable linkedin-chrome.service.
#
# REQUIRES SUDO (run manually):
#   sudo loginctl enable-linger clawdbot
#   sudo chown clawdbot:clawdbot /home/clawdbot/openoutreach/linkedin/tasks/poll_signals.py \
#     /home/clawdbot/openoutreach/linkedin/tasks/scheduler.py \
#     /home/clawdbot/openoutreach/linkedin/tasks/inject_signal_profiles.py \
#     /home/clawdbot/openoutreach/linkedin/tasks/recompute_signal_scores.py

set -euo pipefail

OPENOUTREACH_DIR="/home/clawdbot/openoutreach"
CHROME_PROFILE_SRC="/tmp/chrome-profile-"
CHROME_PROFILE_DST="/home/clawdbot/.chrome-linkedin-profile"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "=== Signal Radar systemd setup ==="

# --- Step 1: Chrome profile migration ---
if [ -d "$CHROME_PROFILE_SRC" ]; then
    echo "[1/4] Migrating Chrome profile from $CHROME_PROFILE_SRC → $CHROME_PROFILE_DST"
    mkdir -p "$CHROME_PROFILE_DST"
    rsync -a "$CHROME_PROFILE_SRC/" "$CHROME_PROFILE_DST/"
    echo "  Done. Size: $(du -sh "$CHROME_PROFILE_DST" | cut -f1)"
else
    echo "[1/4] Chrome profile source $CHROME_PROFILE_SRC not found — skipping migration"
fi

# --- Step 2: Ensure systemd unit directory ---
mkdir -p "$SYSTEMD_USER_DIR"

# --- Step 3: Retire old linkedin-chrome.service if present ---
if [ -f "$SYSTEMD_USER_DIR/linkedin-chrome.service" ] && [ ! -L "$SYSTEMD_USER_DIR/linkedin-chrome.service" ]; then
    echo "[2/4] Retiring old linkedin-chrome.service..."
    systemctl --user disable --now linkedin-chrome.service 2>/dev/null || true
    mv "$SYSTEMD_USER_DIR/linkedin-chrome.service" "$SYSTEMD_USER_DIR/linkedin-chrome.service.disabled.bak"
    systemctl --user mask linkedin-chrome.service 2>/dev/null || true
    echo "  Retired."
else
    echo "[2/4] linkedin-chrome.service already retired — skipping"
fi

# --- Step 4: Write openoutreach-rundaemon.service (no Chrome dependency) ---
echo "[3/4] Writing openoutreach-rundaemon.service..."
cat > "$SYSTEMD_USER_DIR/openoutreach-rundaemon.service" << "EOF"
[Unit]
Description=OpenOutreach Signal Radar Daemon
After=network.target

[Service]
WorkingDirectory=/home/clawdbot/openoutreach
Environment="VIRTUAL_ENV=/home/clawdbot/openoutreach/.venv"
Environment="PATH=/home/clawdbot/openoutreach/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/home/clawdbot/openoutreach/.venv/bin/python \
    /home/clawdbot/openoutreach/manage.py rundaemon
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

# --- Step 5: Reload systemd ---
echo "[4/4] Reloading systemd user daemon..."
systemctl --user daemon-reload

echo ""
echo "=== Setup complete ==="
echo ""
echo "MANUAL STEPS REQUIRED (sudo):"
echo "  1. sudo loginctl enable-linger clawdbot"
echo "     (allows user services to survive logout and start at boot)"
echo "  2. sudo chown clawdbot:clawdbot \\"
echo "       /home/clawdbot/openoutreach/linkedin/tasks/poll_signals.py \\"
echo "       /home/clawdbot/openoutreach/linkedin/tasks/scheduler.py \\"
echo "       /home/clawdbot/openoutreach/linkedin/tasks/inject_signal_profiles.py \\"
echo "       /home/clawdbot/openoutreach/linkedin/tasks/recompute_signal_scores.py"
echo ""
echo "DEPLOY PHASE (when ready to start polling):"
echo "  systemctl --user enable --now openoutreach-rundaemon.service"
echo ""
echo "CHROME IS ON-DEMAND:"
echo "  Before polling, ensure CDP is healthy:"
echo "    bash /home/clawdbot/openoutreach/linkedin/scripts/cdp-health-check.sh"
echo "  If not healthy, recover on-demand:"
echo "    bash /home/clawdbot/openoutreach/linkedin/scripts/cdp-recovery.sh"
