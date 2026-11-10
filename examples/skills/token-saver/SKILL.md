---
name: token-saver
description: Working habits that keep an agent's context small. Use at the start of any coding or analysis task, before the first file read.
---
# Token saver

Every byte you read stays in your context and is paid again on every later call. Work so that
little enters it.

1. Search before you read. Find the place with grep or glob, then read only those lines.
2. Read line ranges, not whole files. Never dump a whole log, JSON file or data file.
3. Send the full output of long or noisy commands to a log file and look at a view of it:
   `tail -n 40`, `grep -c`, `grep -m 20`. If the view was too narrow, read more from the file.
   Do not rerun the command to see more.
4. Batch independent checks into one shell call, separated by `echo` lines.
5. Do not re-read a file after you edited it. The edit would have failed if it had not applied.
6. After a failed check, make one bounded repair attempt, then report.
7. Keep reports short: numbers with their source, no restated context.
