#!/usr/bin/env bash
# bgwait.sh <log> [seconds]
# Wait up to <seconds> (default 240, capped at 270) for the "BGRUN DONE" line that bgrun.sh
# writes, then print the tail of the log. Exit 0 = job done, 1 = still running, 2 = usage.
# Keep each wait under the 5-minute prompt-cache TTL: the agent polls again and stays warm.
set -u
if [ "$#" -lt 1 ]; then
  echo "usage: bgwait.sh <log> [seconds]" >&2
  exit 2
fi
log="$1"
limit="${2:-240}"
if [ "$limit" -gt 270 ]; then
  limit=270
fi
waited=0
while [ "$waited" -lt "$limit" ]; do
  if [ -f "$log" ] && grep -q '^BGRUN DONE rc=' "$log"; then
    tail -n 20 "$log"
    exit 0
  fi
  sleep 5
  waited=$((waited + 5))
done
echo "BGRUN still running after ${limit}s; last lines:"
if [ -f "$log" ]; then
  tail -n 5 "$log"
fi
exit 1
