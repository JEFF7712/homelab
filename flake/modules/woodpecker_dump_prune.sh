# Keep the newest COUNT daily Woodpecker dumps (woodpecker-YYYYMMDD.sqlite)
# in DIR and delete the rest. Retention counts dumps instead of their age, so
# days without a dump never remove the last good copy. The date stamp orders
# the dumps; the woodpecker-latest.sqlite link, partial *.tmp.sqlite files and
# other files are never touched.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: woodpecker-dump-prune DIR COUNT" >&2
  exit 2
fi
dir="$1"
keep="$2"
case "$keep" in
'' | *[!0-9]* | 0)
  echo "retained dump count must be a positive integer, got '$keep'" >&2
  exit 2
  ;;
esac

find "$dir" -maxdepth 1 -type f \
  -name 'woodpecker-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].sqlite' -printf '%f\n' |
  sort -r |
  tail -n +"$((keep + 1))" |
  while IFS= read -r name; do
    rm -f -- "${dir:?}/$name"
    echo "removed old dump $name"
  done
