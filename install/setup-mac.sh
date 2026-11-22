#!/bin/bash
# TonieTausch – Installation / Update für macOS (Apple Silicon & Intel)
# Aufruf aus dem Repo:  bash install/setup-mac.sh
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$HOME/Documents/TonieTausch-Daten"
OLD="$HOME/Desktop/TonieTausch-Daten"
APP="$HOME/Desktop/TonieTausch.app"
LOG=""

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\n[FEHLER] %s\n' "$*"; [ -n "$LOG" ] && printf 'Log: %s\n' "$LOG"; exit 1; }

[ -f "$REPO/app/server.py" ] || die "app/server.py nicht gefunden. Bitte aus dem Repo-Ordner starten."

# Laufende Version beenden
curl -fs -m 5 -X POST http://127.0.0.1:8765/api/beenden -H 'Content-Type: application/json' -d '{}' >/dev/null 2>&1 || true
sleep 1

# Daten vom Schreibtisch nach Dokumente umziehen (alte Installation)
if [ -d "$OLD" ]; then
  if [ -e "$ROOT" ]; then
    echo "Hinweis: $ROOT existiert bereits, $OLD wird nicht verschoben."
  else
    say "Daten nach Dokumente verschieben"
    mv "$OLD" "$ROOT" || die "Verschieben fehlgeschlagen."
  fi
fi
mkdir -p "$ROOT"
LOG="$ROOT/installation.log"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

# Reste der alten Installation entfernen
rm -f "$ROOT/server.py" "$ROOT/index.html" "$ROOT/icon.py"
if [ -f "$ROOT/venv/bin/tonietoolbox" ] && ! head -1 "$ROOT/venv/bin/tonietoolbox" | grep -qF "$ROOT/venv/"; then
  rm -rf "$ROOT/venv"
fi

# Homebrew
BREW=""
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && BREW="$b" && break; done
if [ -z "$BREW" ]; then
  say "Homebrew installieren (fragt nach deinem Mac-Passwort)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    || die "Homebrew-Installation fehlgeschlagen."
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && BREW="$b" && break; done
fi
[ -n "$BREW" ] || die "Homebrew nicht gefunden."
eval "$("$BREW" shellenv)"

say "Python, ffmpeg und opus-tools installieren"
brew install python@3.12 ffmpeg opus-tools || die "brew install fehlgeschlagen."
PY="$(brew --prefix python@3.12)/bin/python3.12"
[ -x "$PY" ] || die "Python 3.12 nicht gefunden."

say "Python-Umgebung und TonieToolbox"
[ -x "$ROOT/venv/bin/python" ] || "$PY" -m venv "$ROOT/venv" || die "Python-Umgebung konnte nicht erstellt werden."
"$ROOT/venv/bin/pip" install --quiet --upgrade pip tonietoolbox pillow || die "Paket-Installation fehlgeschlagen."

say "Programm kopieren"
rm -rf "$ROOT/app"
cp -R "$REPO/app" "$ROOT/app" || die "Kopieren fehlgeschlagen."

say "App auf dem Schreibtisch erstellen"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cat > "$APP/Contents/MacOS/TonieTausch" <<EOF
#!/bin/bash
exec "$ROOT/venv/bin/python" "$ROOT/app/start.py"
EOF
chmod +x "$APP/Contents/MacOS/TonieTausch"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>TonieTausch</string>
  <key>CFBundleDisplayName</key><string>TonieTausch</string>
  <key>CFBundleIdentifier</key><string>local.tonietausch</string>
  <key>CFBundleExecutable</key><string>TonieTausch</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
</dict></plist>
EOF
"$ROOT/venv/bin/python" "$ROOT/app/icon.py" "$APP/Contents/Resources/AppIcon.icns" || echo "Hinweis: Icon konnte nicht erstellt werden."
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
touch "$APP"

say "Fertig! Doppelklick auf TonieTausch auf dem Schreibtisch."
echo "Daten: $ROOT"
