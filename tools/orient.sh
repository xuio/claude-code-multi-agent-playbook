#!/usr/bin/env bash
# orient.sh: the coordinator's one orientation report after a compaction (R-loop-16).
#
# Read the rules digest once, then run this script. It prints, each under a one-line heading:
#   1. the hints lines newer than the digest's "covers through line N" header,
#   2. the first 40 lines of the state file (resume sheet),
#   3. the lane-ledger rows whose state column is "live",
#   4. the last 8 lines of the duties log,
#   5. one line per capped file: its size and longest line against the caps, OK or OVER.
# A missing file prints "(missing: <path>)" and the report goes on. Exit status is always 0:
# this is a report, not a check.
#
# Every setting below can be overridden from the environment, e.g.
#   ORIENT_LEDGER=ops/lanes.md tools/orient.sh
# Defaults follow the file names of STARTER-KIT section 2. Relative paths are taken from
# ORIENT_ROOT.

ORIENT_ROOT="${ORIENT_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
ORIENT_DIGEST="${ORIENT_DIGEST:-ops/rules-digest.md}"
ORIENT_HINTS="${ORIENT_HINTS:-ops/hints.md}"
ORIENT_STATE="${ORIENT_STATE:-notes/resume.md}"
ORIENT_LEDGER="${ORIENT_LEDGER:-ops/lanes.md}"
ORIENT_DUTIES="${ORIENT_DUTIES:-ops/duties.log}"
ORIENT_DIGEST_MAX_BYTES="${ORIENT_DIGEST_MAX_BYTES:-15000}"
ORIENT_DIGEST_MAX_LINE="${ORIENT_DIGEST_MAX_LINE:-400}"
ORIENT_STATE_LINES="${ORIENT_STATE_LINES:-40}"
ORIENT_DUTIES_LINES="${ORIENT_DUTIES_LINES:-8}"

abs() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s\n' "$ORIENT_ROOT/$1" ;;
  esac
}

digest=$(abs "$ORIENT_DIGEST")
hints=$(abs "$ORIENT_HINTS")
state=$(abs "$ORIENT_STATE")
ledger=$(abs "$ORIENT_LEDGER")
duties=$(abs "$ORIENT_DUTIES")

# 1. Hints lines newer than the digest header.
echo "== hints newer than the digest =="
if [ ! -f "$digest" ]; then
  echo "(missing: $digest)"
elif [ ! -f "$hints" ]; then
  echo "(missing: $hints)"
else
  n=$(grep -m 1 -o 'covers through[^0-9]*[0-9][0-9]*' "$digest" | grep -o '[0-9][0-9]*$')
  if [ -z "$n" ]; then
    echo "(no \"covers through line N\" header in $digest)"
  else
    total=$(wc -l < "$hints" | tr -d ' ')
    echo "(digest covers through line $n; hints file has $total lines)"
    tail -n +"$((n + 1))" "$hints" | awk -v start="$((n + 1))" '{ printf "%d: %s\n", start + NR - 1, $0 }'
  fi
fi

# 2. Head of the state file.
echo "== state (first $ORIENT_STATE_LINES lines) =="
if [ -f "$state" ]; then
  head -n "$ORIENT_STATE_LINES" "$state"
else
  echo "(missing: $state)"
fi

# 3. Live lanes: markdown table rows whose "state" column is "live". The first row of each
# table is its header, so a second table with another layout is read by its own header.
echo "== live lanes =="
if [ -f "$ledger" ]; then
  awk -F'|' '
    /^[[:space:]]*\|/ {
      if (!intable) {
        intable = 1; col = 0
        for (i = 1; i <= NF; i++) {
          c = $i; gsub(/^[ \t]+|[ \t]+$/, "", c)
          if (tolower(c) == "state") { col = i; seen = 1; print; break }
        }
        next
      }
      if (col == 0) next
      c = $col; gsub(/^[ \t]+|[ \t]+$/, "", c)
      if (c == "live") { print; found++ }
      next
    }
    { intable = 0 }
    END {
      if (!seen) print "(no table with a \"state\" column)"
      else if (found == 0) print "(no live lanes)"
    }
  ' "$ledger"
else
  echo "(missing: $ledger)"
fi

# 4. Tail of the duties log.
echo "== duties log (last $ORIENT_DUTIES_LINES lines) =="
if [ -f "$duties" ]; then
  tail -n "$ORIENT_DUTIES_LINES" "$duties"
else
  echo "(missing: $duties)"
fi

# 5. Capped files. Add a cap_line call per file that has a cap.
echo "== caps =="
cap_line() { # <name> <path> <max bytes> <max line bytes>
  if [ ! -f "$2" ]; then
    echo "$1: (missing: $2)"
    return
  fi
  bytes=$(wc -c < "$2" | tr -d ' ')
  longest=$(LC_ALL=C awk '{ if (length($0) > m) m = length($0) } END { print m + 0 }' "$2")
  verdict=OK
  if [ "$bytes" -gt "$3" ] || [ "$longest" -gt "$4" ]; then
    verdict=OVER
  fi
  echo "$1: $bytes B of $3, longest line $longest of $4: $verdict"
}
cap_line digest "$digest" "$ORIENT_DIGEST_MAX_BYTES" "$ORIENT_DIGEST_MAX_LINE"

exit 0
