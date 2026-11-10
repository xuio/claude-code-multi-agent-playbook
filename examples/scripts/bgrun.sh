#!/usr/bin/env bash
# bgrun.sh <log> <cmd...>
# Run a long command with all of its output in a log file and mark the end with one line.
# Start it through the harness's run-in-background, then poll with bgwait.sh.
set -u
if [ "$#" -lt 2 ]; then
  echo "usage: bgrun.sh <log> <cmd...>" >&2
  exit 2
fi
log="$1"
shift
mkdir -p "$(dirname "$log")"
echo "BGRUN START $(date '+%Y-%m-%d %H:%M:%S') $*" > "$log"
"$@" >> "$log" 2>&1
rc=$?
echo "BGRUN DONE rc=$rc $(date '+%Y-%m-%d %H:%M:%S')" >> "$log"
exit "$rc"
