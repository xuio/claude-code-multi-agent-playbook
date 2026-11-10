#!/bin/sh
# Entry point of the playbook hooks when they are installed as a plugin.
#
# The plugin is installed once for all projects, so it must do nothing in a project that has
# not opted in. A project opts in with files in <project>/.claude/hooks/:
#   context_guard.json    -> context guard and compaction log
#   bash_guard.json       -> Bash guard
#   after-compaction.txt  -> reminder after a compaction
# The files are searched upwards from the project folder, so a session started in a worktree
# below the project (for example <project>/.claude/worktrees/<name>) finds the project's files.
# Without the file: exit 0 at once, no output. Any failure: exit 0, no output. The one
# exception is the Bash guard's own block (exit 2 with the reason on stderr).
#
# A hook that the project registers itself is left to the project: when a "command" line in the
# project's settings names the script (or the reminder file), the plugin does nothing for that
# hook. So nothing runs twice while a project moves from copied scripts to the plugin, and the
# plugin takes over as soon as the entry is removed from the project's settings.

here=$(dirname "$0")

find_up() {   # $1 = file name; prints the .claude/hooks folder that holds it
  d=${CLAUDE_PROJECT_DIR:-$PWD}
  while [ -n "$d" ]; do
    if [ -f "$d/.claude/hooks/$1" ]; then
      printf '%s\n' "$d/.claude/hooks"
      return 0
    fi
    [ "$d" = "/" ] && break
    d=$(dirname "$d")
  done
  return 1
}

own() {       # $1 = name as a grep pattern, $2 = the .claude/hooks folder found; true = the project runs it
  root=$(dirname "$(dirname "$2")")
  for f in "$root/.claude/settings.json" "$root/.claude/settings.local.json" \
           "${CLAUDE_PROJECT_DIR:-$PWD}/.claude/settings.json" \
           "${CLAUDE_PROJECT_DIR:-$PWD}/.claude/settings.local.json"; do
    grep -qs "\"command\".*$1" "$f" && return 0
  done
  return 1
}

case "$1" in
  guard)
    dir=$(find_up context_guard.json) || exit 0
    own 'context_guard\.py' "$dir" && exit 0
    python3 -I "$here/context_guard.py" --config "$dir/context_guard.json" 2>/dev/null || true
    ;;
  bash-guard)
    dir=$(find_up bash_guard.json) || exit 0
    own 'bash_guard\.py' "$dir" && exit 0
    python3 -I "$here/bash_guard.py" --config "$dir/bash_guard.json"
    [ $? -eq 2 ] && exit 2
    ;;
  after-compaction)
    dir=$(find_up after-compaction.txt) || exit 0
    own 'after-compaction\.txt' "$dir" && exit 0
    cat "$dir/after-compaction.txt" 2>/dev/null || true
    ;;
  compaction-log)
    dir=$(find_up context_guard.json) || exit 0
    own 'compaction_log\.py' "$dir" && exit 0
    python3 -I "$here/compaction_log.py" >/dev/null 2>&1 || true
    ;;
esac
exit 0
