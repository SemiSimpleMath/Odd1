#!/usr/bin/env bash
# The housekeeper's only way to delete files: those owned by agent and unchanged for over 7 days, in ~/.cache or /tmp.
#   ./clean.sh cache | ./clean.sh tmp      prints what it removed (count, MB) and the space now free
set -euo pipefail
case "${1:-}" in
  cache) dir=/home/agent/.cache ;;
  tmp) dir=/tmp ;;
  *) echo "usage: clean.sh cache|tmp" >&2; exit 2 ;;
esac
list=$(mktemp)
trap 'rm -f "$list"' EXIT
find "$dir" -xdev -mindepth 1 -user agent \( -type f -o -type l \) -mtime +7 -printf '%s\t%p\n' 2>/dev/null >"$list" || true
count=$(wc -l <"$list")
mb=$(awk -F'\t' '{s+=$1} END {printf "%.1f", s/1048576}' "$list")
cut -f2- "$list" | while IFS= read -r f; do rm -f -- "$f"; done
find "$dir" -xdev -mindepth 2 -user agent -type d -empty -mmin +60 -delete 2>/dev/null || true
echo "removed $count files ($mb MB) older than 7 days from $dir"
df -h "$dir" | tail -1
