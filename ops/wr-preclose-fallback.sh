#!/usr/bin/env bash
# Run the existing runner-1 stock_ci.sh only when NYSE preclose state is missing.
set -euo pipefail
export TZ=Asia/Ho_Chi_Minh
STATE_DIR="${WR_FALLBACK_STATE_DIR:-/var/lib/wr-preclose-fallback}"
PY_BIN="${WR_FALLBACK_PY_BIN:-/opt/wr-preclose-fallback/venv/bin/python3}"
GH_BIN="${WR_FALLBACK_GH_BIN:-/usr/bin/gh}"
mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/lock"
if ! flock -n 9; then echo 'WR_PRE_CLOSE=BUSY_ALREADY_RUNNING'; exit 0; fi
write_status() {
  local result="$1" note="${2:-}"
  "$PY_BIN" - "$STATE_DIR/last-status.json" "$result" "$note" <<'PY'
import datetime,json,os,pathlib,sys,tempfile
path=pathlib.Path(sys.argv[1])
val={'schema_version':'wr-vps-preclose-fallback-v1','status':sys.argv[2],
     'reason':sys.argv[3], 'observed_at_vn':datetime.datetime.now(
     datetime.timezone(datetime.timedelta(hours=7))).isoformat()}
fd,tmp=tempfile.mkstemp(prefix='status.',suffix='.tmp',dir=path.parent)
try:
    with os.fdopen(fd,'w') as f:
        json.dump(val,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
finally:
    if os.path.exists(tmp):os.unlink(tmp)
PY
}
if [[ "${1:-}" == "--self-test" ]]; then
  "$PY_BIN" - <<'PY'
import requests,websocket,pandas_market_calendars
from zoneinfo import ZoneInfo
from datetime import datetime,timezone
print('WR_PRE_CLOSE_SELFTEST=PASS',datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York')).isoformat())
PY
  "$GH_BIN" api repos/louisalviss/runner-1 --jq .full_name
  echo 'WR_PRE_CLOSE_NO_MUTATION=PASS'
  exit 0
fi
et_day="$(TZ=America/New_York date +%u)"
et_clock="$(TZ=America/New_York date +%H%M)"
if (( et_day > 5 || 10#$et_clock < 1545 || 10#$et_clock > 1559 )); then
  echo "WR_PRE_CLOSE=SKIP_OUTSIDE_WINDOW clock=$et_clock"
  write_status skip outside_new_york_preclose_window
  exit 0
fi
workdir="$(mktemp -d "$STATE_DIR/work.XXXXXXXX")"
trap 'rm -rf -- "$workdir"' EXIT
"$GH_BIN" repo clone louisalviss/runner-1 "$workdir/runner-1" -- --depth=1 --quiet
cd "$workdir/runner-1"
git config --local credential.helper '!gh auth git-credential'
export PATH="$(dirname "$PY_BIN"):$PATH"
set +e
"$PY_BIN" - <<'PY'
import sys,stock_runner as r
now=r.now_utc()
today=now.astimezone(r.NY).date().isoformat()
print('WR_PRE_CLOSE_MARKET_DATE',today)
if not r.market_open_now(now):
    print('WR_PRE_CLOSE=SKIP_NYSE_NOT_OPEN')
    sys.exit(10)
prior=r.read_slot('1')
if prior and prior.get('session_date_us')==today and prior.get('finalized') and prior.get('finalized_by_mode')=='preclose':
    print('WR_PRE_CLOSE=SKIP_ALREADY_FINALIZED',today)
    sys.exit(11)
sys.exit(0)
PY
preflight_rc=$?
set -e
case "$preflight_rc" in
  10) write_status skip market_closed; exit 0 ;;
  11) write_status success already_finalized; exit 0 ;;
  0) ;;
  *) write_status failure preflight_error; exit "$preflight_rc" ;;
esac
echo 'WR_PRE_CLOSE=RUN_CORE_SOURCE'
set +e
EVENT_NAME=workflow_dispatch INPUT_M=3 EVENT_SCHEDULE='' bash stock_ci.sh
run_rc=$?
set -e
if (( run_rc != 0 )); then
  write_status failure "core_runner_exit_$run_rc"
  echo "WR_PRE_CLOSE=FAILED_CORE_RC_$run_rc"
  exit "$run_rc"
fi
"$PY_BIN" - <<'PY'
import stock_runner as r
now=r.now_utc()
today=now.astimezone(r.NY).date().isoformat()
final=r.read_slot('1')
assert final and final.get('session_date_us')==today, 'fresh preclose missing'
assert final.get('finalized') is True and final.get('finalized_by_mode')=='preclose'
assert isinstance(final.get('rows'),list) and len(final['rows'])>=100
print('WR_PRE_CLOSE_LOCAL_FINAL=PASS',today,len(final['rows']))
PY
git fetch -q origin main
cmp o/1 <(git show origin/main:o/1)
echo 'WR_PRE_CLOSE_REMOTE_READBACK=PASS'
write_status success newly_finalized_readback_verified
