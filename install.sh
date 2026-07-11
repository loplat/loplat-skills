#!/usr/bin/env bash
# loplat-skills 설치 스크립트 — 스코프(사용자/프로젝트)는 사용자가 선택한다.
#
# 사용법:
#   ./install.sh --user [--link] [--dry-run] [skill ...]
#   ./install.sh --project <dir> [--link] [--dry-run] [skill ...]
#   ./install.sh --list
#
#   --user       사용자 스코프: ~/.claude/skills/, ~/.agents/skills/
#   --project    프로젝트 스코프: <dir>/.claude/skills/, <dir>/.agents/skills/, <dir>/.cursor/skills/
#   --link       copy 대신 symlink (git pull 만으로 갱신됨; 개인 머신 권장)
#   --dry-run    설치하지 않고 대상 경로만 출력
#   skill 미지정 시 전체 스킬 설치.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$REPO_DIR/.apm/skills"

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

MODE="" PROJECT_DIR="" LINK=0 DRY=0 SKILLS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --user) MODE=user ;;
    --project) MODE=project; PROJECT_DIR="${2:?--project <dir> 필요}"; shift ;;
    --list) ls -1 "$SRC_DIR"; exit 0 ;;
    --link) LINK=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) usage ;;
    -*) echo "알 수 없는 옵션: $1"; usage ;;
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
  echo "[i] 사용자 스코프 설치. Cursor는 프로젝트 스코프(.cursor/skills)만 표준이므로 --project 를 사용하세요."
else
  PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
  TARGETS=("$PROJECT_DIR/.claude/skills" "$PROJECT_DIR/.agents/skills" "$PROJECT_DIR/.cursor/skills")
fi

command -v rsync >/dev/null || { echo "[x] rsync 필요"; exit 2; }

for skill in "${SKILLS[@]}"; do
  src="$SRC_DIR/$skill"
  [ -d "$src" ] || { echo "[x] 스킬 없음: $skill (--list 로 확인)"; exit 2; }
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

[ "$DRY" = 1 ] || echo "[done] ${#SKILLS[@]}개 스킬 설치 완료. 갱신: git pull 후 재실행$( [ $LINK = 1 ] && echo ' (symlink는 pull만으로 갱신됨)' )"
