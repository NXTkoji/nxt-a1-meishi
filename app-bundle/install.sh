#!/bin/bash
# Install the tracked .app bundle over the one the Dock actually launches.
#
# The repo copy is canonical. The Dock points at a bundle in the project root,
# one level above this repo, which is outside version control — so edit the
# tracked copy and run this to push it live.
#
# Usage: ./app-bundle/install.sh
set -euo pipefail

APP_NAME="NXT名刺整理器.app"

# Resolve everything from this script's own location, so the checkout can move
# and the script can be run from any working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$REPO_ROOT/.." && pwd)"

SRC="$SCRIPT_DIR/$APP_NAME"
DEST="$PROJECT_ROOT/$APP_NAME"

# ── Validate the source ───────────────────────────────────────────────────────
if [ ! -d "$SRC" ]; then
  echo "✗ Tracked bundle not found: $SRC" >&2
  exit 1
fi
if [ ! -f "$SRC/Contents/Info.plist" ] || [ ! -d "$SRC/Contents/MacOS" ]; then
  echo "✗ Source does not look like an app bundle: $SRC" >&2
  exit 1
fi

# ── Already in sync? Then do nothing ─────────────────────────────────────────
# A no-op run must not restart the Dock, so check before touching anything.
if [ -d "$DEST" ] && diff -r "$SRC" "$DEST" >/dev/null 2>&1; then
  echo "✓ Already in sync — nothing to do."
  echo "  $DEST"
  exit 0
fi

# Did the icon specifically change? Only an icon change justifies the
# disruption of restarting the Dock.
icon_changed=1
if [ -f "$DEST/Contents/Resources/AppIcon.icns" ] \
   && cmp -s "$SRC/Contents/Resources/AppIcon.icns" "$DEST/Contents/Resources/AppIcon.icns"; then
  icon_changed=0
fi

# ── Guard the destructive step ───────────────────────────────────────────────
# Replacing the destination means rm -rf on a derived path. Refuse unless it is
# exactly where we expect: named correctly, directly inside the project root,
# and already a real app bundle. Anything else aborts rather than deleting.
if [ -e "$DEST" ]; then
  case "$DEST" in
    "$PROJECT_ROOT/$APP_NAME") ;;
    *) echo "✗ Refusing to replace unexpected path: $DEST" >&2; exit 1 ;;
  esac
  if [ ! -d "$DEST" ] || [ ! -f "$DEST/Contents/Info.plist" ]; then
    echo "✗ Destination exists but is not an app bundle, refusing to delete: $DEST" >&2
    exit 1
  fi
  rm -rf "$DEST"
fi

# ── Install ──────────────────────────────────────────────────────────────────
# -R preserves the 755 bit on Contents/MacOS/<launcher>.
cp -R "$SRC" "$DEST"
echo "✓ Installed $APP_NAME"
echo "  from $SRC"
echo "  to   $DEST"

# Re-register so LaunchServices picks up the bundle's current metadata.
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
touch "$DEST"
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$DEST" 2>/dev/null || true

# ── Refresh the icon, if it changed ──────────────────────────────────────────
# The Dock keeps a private cache of rendered tiles that survives a restart, so
# `killall Dock` on its own reloads the same stale icon. The cache must go first.
if [ "$icon_changed" -eq 1 ]; then
  DOCK_CACHE="$(getconf DARWIN_USER_CACHE_DIR)com.apple.dock.iconcache"
  rm -f "$DOCK_CACHE"
  killall Dock 2>/dev/null || true
  echo "✓ Icon changed — cleared the Dock icon cache and restarted the Dock."
else
  echo "· Icon unchanged — left the Dock alone."
fi
