#!/bin/bash
# Deploy backend.
#
# NEVER run uvicorn manually — always use this script.
# KeepAlive=true means any manually started process creates a duplicate that
# launchctl doesn't know about and will never kill.

PLIST=~/Library/LaunchAgents/co.nxta.nxt-a1-backend.plist

echo "Stopping backend..."

# Step 1: Tell launchd to stop managing the process (suppresses auto-respawn)
launchctl unload "$PLIST" 2>/dev/null || true

# Step 2: Kill ALL uvicorn processes for this app — launchctl unload is
# unreliable and may leave orphans, or prior manual runs may exist.
pkill -f "uvicorn app.main:app" 2>/dev/null || true

# Step 3: Wait until port 8000 is clear of Python processes
for i in $(seq 1 10); do
  PYTHON_ON_PORT=$(lsof -ti :8000 2>/dev/null | xargs -I{} ps -p {} -o command= 2>/dev/null | grep -c python || true)
  if [ "$PYTHON_ON_PORT" = "0" ]; then
    break
  fi
  echo "  ...waiting for port to clear ($i/10)"
  sleep 1
done

echo "Starting backend..."
launchctl load "$PLIST"

echo "Waiting for startup..."
for i in $(seq 1 10); do
  sleep 1
  RESPONSE=$(curl -s http://localhost:8000/api/v1/health 2>/dev/null || true)
  if [ "$RESPONSE" = '{"status":"ok"}' ]; then
    NEW_PID=$(lsof -ti :8000 2>/dev/null | xargs -I{} ps -p {} -o pid= 2>/dev/null | grep -v "^$" | head -1 || true)
    echo "✓ Backend up (pid=$NEW_PID)"
    exit 0
  fi
  echo "  ...waiting ($i/10)"
done

echo "✗ Backend failed to start. Check: tail -50 /tmp/nxt-a1-backend.log"
exit 1
