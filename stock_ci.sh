#!/usr/bin/env bash
set -u

mkdir -p diagnostics

event_name="${EVENT_NAME:-}"
event_schedule="${EVENT_SCHEDULE:-}"
input_m="${INPUT_M:-8}"
mode=""

if [[ "$event_name" == "push" ]]; then
  mode=probe
elif [[ "$event_name" == "workflow_dispatch" ]]; then
  case "$input_m" in
    1) mode=main ;;
    2) mode=mid ;;
    3) mode=preclose ;;
    4) mode=smoothness ;;
    8|*)
      read et_hour et_minute wait_seconds <<<"$(python3 - <<'PY'
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
x = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
minute = x.hour * 60 + x.minute
wait = 0
if 9 * 60 + 30 <= minute < 10 * 60 + 30:
    target = x.replace(hour=10, minute=30, second=0, microsecond=0)
    wait = max(0, int((target - x).total_seconds()))
print(x.hour, x.minute, wait)
PY
)"
      if [[ "$wait_seconds" -gt 0 ]]; then
        printf '%s\n' "$wait_seconds" > diagnostics/runner1_wait_seconds.txt
        sleep "$wait_seconds"
      fi
      mode=auto
      ;;
  esac
else
  read et_hour et_minute <<<"$(python3 - <<'PY'
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
x = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
print(x.hour, x.minute)
PY
)"
  if [[ "$event_schedule" == "30 14 * * 1-5" || "$event_schedule" == "30 15 * * 1-5" ]]; then
    [[ "$et_hour" == "10" && "$et_minute" -ge 30 ]] && mode=main || mode=noop
  elif [[ "$event_schedule" == "45 16 * * 1-5" || "$event_schedule" == "45 17 * * 1-5" ]]; then
    [[ "$et_hour" == "12" && "$et_minute" -ge 45 ]] && mode=mid || mode=noop
  elif [[ "$event_schedule" == "45 19 * * 1-5" || "$event_schedule" == "45 20 * * 1-5" ]]; then
    [[ "$et_hour" == "15" && "$et_minute" -ge 45 ]] && mode=preclose || mode=noop
  elif [[ "$event_schedule" == "15 22 * * 1-5" ]]; then
    # Canonical post-close schedule is fixed in Vietnam time, not New York time.
    # 22:15 UTC Monday-Friday = 05:15 Asia/Ho_Chi_Minh Tuesday-Saturday year-round.
    mode=smoothness
  else
    mode=noop
  fi
fi

printf '%s\n' "$mode" > diagnostics/runner1_resolved_mode.txt
printf '%s\n' "$(date -u +%FT%TZ)" > diagnostics/runner1_started_utc.txt
printf '%s\n' "$event_name" > diagnostics/runner1_event_name.txt
printf '%s\n' "$event_schedule" > diagnostics/runner1_event_schedule.txt

rc=0
if [[ "$mode" != "noop" ]]; then
  set +e
  python3 -m pip install --disable-pip-version-check -r requirements.txt >diagnostics/runner1_install_stdout.txt 2>diagnostics/runner1_install_stderr.txt
  install_rc=$?
  printf '%s\n' "$install_rc" > diagnostics/runner1_install_exit_code.txt
  if [[ "$install_rc" != "0" ]]; then
    rc=$install_rc
  else
    python3 stock_runner_entry.py --mode "$mode" >diagnostics/runner1_last_stdout.txt 2>diagnostics/runner1_last_stderr.txt
    rc=$?
  fi
  set -e
fi

printf '%s\n' "$rc" > diagnostics/runner1_last_exit_code.txt
printf '%s\n' "$(date -u +%FT%TZ)" > diagnostics/runner1_finished_utc.txt

git config user.name "wave-rider-stock-runner[bot]"
git config user.email "wave-rider-stock-runner[bot]@users.noreply.github.com"
git add -A diagnostics
if [[ -d o ]]; then
  git add -A o
fi
if ! git diff --cached --quiet; then
  git commit -m "data: Stock runner-1 state [skip ci]"
  pushed=0
  for attempt in 1 2 3 4 5; do
    if git pull --rebase origin main && git push origin HEAD:main; then
      pushed=1
      break
    fi
    git rebase --abort 2>/dev/null || true
    sleep $((attempt * 2))
  done
  if [[ "$pushed" != "1" ]]; then
    echo "Failed to persist runner-1 state" >&2
    exit 91
  fi
fi

exit "$rc"
