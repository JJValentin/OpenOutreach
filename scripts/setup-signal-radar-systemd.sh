#!/usr/bin/env bash
# setup-signal-radar-systemd.sh
# Idempotent setup script for Signal Radar systemd services.
# Run as clawdbot user on MindPalace.
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
    echo "[1/5] Migrating Chrome profile from $CHROME_PROFILE_SRC → $CHROME_PROFILE_DST"
    mkdir -p "$CHROME_PROFILE_DST"
    rsync -a "$CHROME_PROFILE_SRC/" "$CHROME_PROFILE_DST/"
    echo "  Done. Size: $(du -sh "$CHROME_PROFILE_DST" | cut -f1)"
else
    echo "[1/5] Chrome profile source $CHROME_PROFILE_SRC not found — skipping migration"
fi

# --- Step 2: Kill existing Chrome processes ---
echo "[2/5] Stopping any existing Chrome processes..."
pkill -f "google-chrome" || true
sleep 2
echo "  Done."

# --- Step 3: Create systemd unit directory ---
mkdir -p "$SYSTEMD_USER_DIR"

# --- Step 4: Write linkedin-chrome.service ---
echo "[3/5] Writing linkedin-chrome.service..."
cat > "$SYSTEMD_USER_DIR/linkedin-chrome.service" << "EOF"
[Unit]
Description=LinkedIn Chrome Browser for Signal Radar CDP
After=network.target

[Service]
Environment=DISPLAY=:99
ExecStart=/usr/bin/google-chrome-stable \
    --remote-debugging-port=9222 \
    --no-first-run \
    --no-default-browser-check \
    --ozone-platform=x11 \
    --disable-gpu \
    --window-size=1280,800 \
    --user-data-dir=/home/clawdbot/.chrome-linkedin-profile/ \
    https://www.linkedin.com/login
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

# --- Step 5: Write openoutreach-rundaemon.service ---
echo "[4/5] Writing openoutreach-rundaemon.service..."
cat > "$SYSTEMD_USER_DIR/openoutreach-rundaemon.service" << "EOF"
[Unit]
Description=OpenOutreach Signal Radar Daemon
After=network.target linkedin-chrome.service
Requires=linkedin-chrome.service

[Service]
WorkingDirectory=/home/clawdbot/openoutreach
ExecStart=/home/clawdbot/openoutreach/.venv/bin/python \
    /home/clawdbot/openoutreach/manage.py rundaemon
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

# --- Step 6: Reload and enable Chrome service ---
echo "[5/5] Reloading systemd user daemon and enabling linkedin-chrome.service..."
systemctl --user daemon-reload
systemctl --user enable --now linkedin-chrome.service
echo "  linkedin-chrome.service enabled."

# NOTE: openoutreach-rundaemon.service is NOT enabled here.
# Enable it only after verifying no unintended WatchedSources are active:
#   python manage.py shell -c "from linkedin.models import WatchedSource; print(WatchedSource.objects.filter(is_active=True).count())"
#   systemctl --user enable --now openoutreach-rundaemon.service

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
