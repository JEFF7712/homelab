#!/bin/sh
# Load one dump into a scratch postgres and require tables with rows.
# Usage: verify-dump.sh <dataset> <dump.gz> <results-dir>
# The result file holds "pass: ..." or "fail: ..."; the script itself always
# exits 0 so every dataset is tried and recorded.
set -u
dataset="$1"
dump="$2"
results="$3"
data="${WORK:-/work}/pg-${dataset}"
cleanup() {
  [ -f "${data}/db/postmaster.pid" ] && pg_ctl --pgdata="${data}/db" --wait --silent stop -m fast >/dev/null 2>&1
  rm -rf "${data}"
}
fail() {
  cleanup
  echo "fail: $*" > "${results}/${dataset}"
  echo "${dataset}: fail: $*"
  exit 0
}
[ -s "${dump}" ] || fail "no dump restored"
rm -rf "${data}" && mkdir -p "${data}/db" "${data}/run"
initdb --pgdata="${data}/db" --auth=trust --username=drill >/dev/null || fail "initdb"
# Immich's vector extensions must be preloaded when the image ships them.
libdir="$(pg_config --pkglibdir 2>/dev/null || true)"
preload=""
for library in vchord vectors; do
  [ -n "${libdir}" ] && [ -f "${libdir}/${library}.so" ] \
    && preload="${preload:+${preload},}${library}.so"
done
pg_ctl --pgdata="${data}/db" --wait --silent --log="${data}/server.log" \
  -o "-c listen_addresses='' -k ${data}/run ${preload:+-c shared_preload_libraries=${preload}}" \
  start >/dev/null || fail "postgres did not start: $(tail -n 2 "${data}/server.log" | tr '\n' ' ')"
export PGHOST="${data}/run" PGUSER=drill PGDATABASE=restored
createdb restored || fail "createdb"
gunzip -c "${dump}" > "${data}/restore.dump" || fail "gunzip"
pg_restore --no-owner --no-privileges --exit-on-error --dbname=restored \
  "${data}/restore.dump" > "${data}/restore.log" 2>&1 \
  || fail "pg_restore: $(tail -n 3 "${data}/restore.log" | tr '\n' ' ')"
psql -q -c 'ANALYZE' >/dev/null || fail "analyze"
# reltuples is set by ANALYZE itself, unlike the asynchronous statistics views.
counts="$(psql -tA -F ' ' -c "
  SELECT count(*), coalesce(sum(greatest(c.reltuples, 0)), 0)::bigint
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r', 'p')
    AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname NOT LIKE 'pg_toast%'")" || fail "count"
tables="${counts% *}"
rows="${counts#* }"
[ "${tables:-0}" -gt 0 ] || fail "no tables restored"
[ "${rows:-0}" -gt 0 ] || fail "no rows restored"
cleanup
echo "pass: $(basename "${dump}") ${tables} tables ${rows} rows" > "${results}/${dataset}"
echo "${dataset}: pass (${tables} tables, ${rows} rows)"
