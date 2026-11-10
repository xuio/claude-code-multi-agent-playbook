#!/usr/bin/env bash
# sweep_timer.sh: wait, run one command, then print the command that starts this timer again.
#
#   sweep_timer.sh <minutes> <command> [args...]
#
# Start it with the Bash tool's background mode (run_in_background). When it ends, the
# completion notice carries its output, and the LAST line, always a line of its own, is
#
#   RE-ARM: <the exact command line that started this timer>
#
# with this script's absolute path and every argument shell-quoted, so that the line can be
# pasted to start the next round. A summary or a carry block may lose the command; the notice
# does not (R-loop-15). Exit status: the command's.
#
# Example (every 30 minutes, the sweep's checks in one script of your own):
#   tools/sweep_timer.sh 30 ops/sweep.sh
# Works with the bash 3.2 that macOS ships.

set -u

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <minutes> <command> [args...]" >&2
  exit 2
fi

minutes=$1
case "$minutes" in
  ''|*[!0-9]*)
    echo "sweep_timer.sh: minutes must be a whole number >= 0, got '$minutes'" >&2
    exit 2
    ;;
esac

# Absolute path of this script, so that RE-ARM works from any folder.
self_dir=$(cd "$(dirname "$0")" && pwd)
self="$self_dir/$(basename "$0")"

# Build the re-arm line before running anything: the command may change the folder or the
# arguments' meaning, the line must not change.
rearm=$(printf '%q' "$self")
for arg in "$@"; do
  rearm="$rearm $(printf '%q' "$arg")"
done

shift
# 10#: read the number as decimal; a leading zero ("08") would make it octal and fail.
sleep $((10#$minutes * 60))

"$@"
rc=$?

# The empty line first: output that does not end in a newline would otherwise carry RE-ARM on
# its last line, and RE-ARM must start a line of its own.
echo
echo "RE-ARM: $rearm"
exit "$rc"
