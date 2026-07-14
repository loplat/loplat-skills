#!/usr/bin/env bash
# loplat-skills installer -- you choose the scope (user / project).
#
# Usage:
#   ./install.sh --user [--link] [--dry-run] [skill ...]
#   ./install.sh --project <dir> [--link] [--dry-run] [skill ...]
#   ./install.sh --list
#
#   --user       User scope: ~/.claude/skills/, ~/.agents/skills/
#   --project    Project scope: <dir>/.claude/skills/, <dir>/.agents/skills/, <dir>/.cursor/skills/
#   --link       Symlink instead of copy (updates on git pull alone; recommended on a personal machine)
#   --dry-run    Print target paths without installing
#   With no skill listed, all skills are installed.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$REPO_DIR/.apm/skills"

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

MODE="" PROJECT_DIR="" LINK=0 DRY=0 SKILLS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --user) MODE=user ;;
    --project) MODE=project; PROJECT_DIR="${2:?--project <dir> required}"; shift ;;
    --list) ls -1 "$SRC_DIR"; exit 0 ;;
    --link) LINK=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $1"; usage ;;
    *) SKILLS+=("$1") ;;
  esac
  shift
done
[ -n "$MODE" ] || usage

if [ ${#SKILLS[@]} -eq 0 ]; then
  while IFS= read -r s; do SKILLS+=("$s"); done < <(ls -1 "$SRC_DIR")
fi

if [ "$MODE" = user ]; then
  TARGETS=("$HOME/.claude/skills" "$HOME/.agents/skills")
  echo "[i] User-scope install. Cursor only standardizes project scope (.cursor/skills); use --project for it."
else
  PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
  TARGETS=("$PROJECT_DIR/.claude/skills" "$PROJECT_DIR/.agents/skills" "$PROJECT_DIR/.cursor/skills")
fi

command -v rsync >/dev/null || { echo "[x] rsync required"; exit 2; }

for skill in "${SKILLS[@]}"; do
  src="$SRC_DIR/$skill"
  [ -d "$src" ] || { echo "[x] no such skill: $skill (see --list)"; exit 2; }
  for tdir in "${TARGETS[@]}"; do
    dst="$tdir/$skill"
    if [ "$DRY" = 1 ]; then
      echo "[dry-run] $skill -> $dst $( [ $LINK = 1 ] && echo '(symlink)' || echo '(copy)' )"
      continue
    fi
    mkdir -p "$tdir"
    if [ "$LINK" = 1 ]; then
      [ -d "$dst" ] && [ ! -L "$dst" ] && rm -rf "$dst"
      ln -sfn "$src" "$dst"
      echo "[ok] $skill -> $dst (symlink)"
    else
      [ -L "$dst" ] && rm -f "$dst"
      rsync -a --delete --exclude '__pycache__' "$src/" "$dst/"
      echo "[ok] $skill -> $dst (copy)"
    fi
  done
done

[ "$DRY" = 1 ] || echo "[done] installed ${#SKILLS[@]} skill(s). Update: git pull, then re-run$( [ $LINK = 1 ] && echo ' (symlink updates on pull alone)' )."
