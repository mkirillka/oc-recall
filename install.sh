#!/usr/bin/env bash
# Install oc-recall: symlink the skill, create the CLI shim, then index past sessions.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="$(basename "$SRC")"
SKILLS_DIR="${OPENCODE_SKILLS_DIR:-$HOME/.config/opencode/skills}"
BIN_DIR="${OC_RECALL_BIN_DIR:-$HOME/.local/bin}"
DRY_RUN=0
FORCE=0
NO_AGENT=0

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

  --dry-run           show what would happen, change nothing
  --force             replace an existing directory or link at the target
  --dest <dir>        skills directory (default: ~/.config/opencode/skills)
  --bin <dir>         where to put the oc-recall command (default: ~/.local/bin)
  --no-agent          do not install the hourly index updater
  -h, --help          this message
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --force) FORCE=1 ;;
        --no-agent) NO_AGENT=1 ;;
        --dest)
            [ $# -ge 2 ] || { echo "install.sh: --dest needs a value" >&2; exit 2; }
            SKILLS_DIR="$2"; shift ;;
        --bin)
            [ $# -ge 2 ] || { echo "install.sh: --bin needs a value" >&2; exit 2; }
            BIN_DIR="$2"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "install.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

echo "source:      $SRC"
echo "skills dir:  $SKILLS_DIR"
echo "command dir: $BIN_DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "install.sh: python3 is required" >&2
    exit 1
fi

mkdir -p "$SKILLS_DIR" "$BIN_DIR"

TARGET="$SKILLS_DIR/$NAME"
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
    if [ "$FORCE" -eq 1 ]; then
        run rm -rf "$TARGET"
    else
        echo "install.sh: $TARGET already exists (use --force to replace)" >&2
        exit 1
    fi
fi
run ln -s "$SRC" "$TARGET"

SHIM="$BIN_DIR/oc-recall"
if [ -e "$SHIM" ] || [ -L "$SHIM" ]; then
    if [ "$FORCE" -eq 1 ]; then
        run rm -f "$SHIM"
    else
        echo "install.sh: $SHIM already exists (use --force to replace)" >&2
        exit 1
    fi
fi
run ln -s "$SRC/scripts/oc_recall.py" "$SHIM"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] python3 $SRC/scripts/oc_recall.py --setup"
    exit 0
fi

SETUP_ARGS=("--setup")
if [ "$NO_AGENT" -eq 1 ]; then
    SETUP_ARGS+=("--no-agent")
fi
python3 "$SRC/scripts/oc_recall.py" "${SETUP_ARGS[@]}"

echo
echo "Installed. Try: oc-recall \"cheap flight with a layover\""
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "note: $BIN_DIR is not on PATH — call $SHIM directly or add it to PATH" ;;
esac
