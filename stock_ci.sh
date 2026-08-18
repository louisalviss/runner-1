#!/usr/bin/env bash
set -u

mkdir -p diagnostics

remote_mark() {
  local phase="$1"
  local text="$2"
  if [[ -z "${GH_TOKEN:-}" || -z "${REPO:-}" || -z "${RUN_ID:-}" ]]; then
    return 0
  fi
  local body encoded payload path
  body="phase=${phase} run=${RUN_ID} at=$(date -u +%FT%TZ) ${text}"
  encoded="$(printf '%s\n' "$body" | base64 -w0)"
  path="diagnostics/stock-${RUN_ID}-${phase}.txt"
  payload="$(printf '{\"message\":\"debug: Stock runner1 phase %s [skip ci]\",\"content\":\"%s\"}' "$phase" "$encoded")"
  curl --fail-with-body -sS -X PUT \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer $GH_TOKEN" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/$REPO/contents/$path" \
    -d "$payload" >/dev/null 2>&1 || true
}

remote_mark "10-script-start" "event=${EVENT_NAME:-}"

event_name="${EVENT_NAME:-}"
event_schedule="${EVENT_SCHEDULE:-}"
input_m="${INPUT_M:-8}"
mode=""

if [[ "$event_name" == "push" || "$event_name" == "issues" ]]; then
  mode=probe
elif [[ "$event_name" == "workflow_dispatch" ]]; then
  case "$input_m" in
    1) mode=main ;;
    2) mode=mid ;;
    3) mode=preclose ;;
    4) mode=smoothness ;;
    8|*) mode=auto ;;
  esac
else
  read et_hour et_minute <<<"$(python3 - <<'PY'
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
x = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
print(x.hour, x.minute)
PY
)"
  if [[ "$event_schedule" == "0 14 * * 1-5" || "$event_schedule" == "0 15 * * 1-5" ]]; then
    [[ "$et_hour" == "10" ]] && mode=main || mode=noop
  elif [[ "$event_schedule" == "45 16 * * 1-5" || "$event_schedule" == "45 17 * * 1-5" ]]; then
    [[ "$et_hour" == "12" && "$et_minute" == "45" ]] && mode=mid || mode=noop
  elif [[ "$event_schedule" == "45 19 * * 1-5" || "$event_schedule" == "45 20 * * 1-5" ]]; then
    [[ "$et_hour" == "15" && "$et_minute" == "45" ]] && mode=preclose || mode=noop
  elif [[ "$event_schedule" == "15 22 * * 1-5" || "$event_schedule" == "15 23 * * 1-5" ]]; then
    [[ "$et_hour" == "18" && "$et_minute" == "15" ]] && mode=smoothness || mode=noop
  else
    mode=noop
  fi
fi

remote_mark "15-mode" "mode=$mode schedule=$event_schedule"

printf '%s\n' "$mode" > diagnostics/runner1_resolved_mode.txt
printf '%s\n' "$(date -u +%FT%TZ)" > diagnostics/runner1_started_utc.txt
printf '%s\n' "$event_name" > diagnostics/runner1_event_name.txt
printf '%s\n' "$event_schedule" > diagnostics/runner1_event_schedule.txt

rc=0
if [[ "$mode" != "noop" ]]; then
  remote_mark "20-install-start" "mode=$mode"
  set +e
  python3 -m pip install --disable-pip-version-check -r requirements.txt >diagnostics/runner1_install_stdout.txt 2>diagnostics/runner1_install_stderr.txt
  install_rc=$?
  printf '%s\n' "$install_rc" > diagnostics/runner1_install_exit_code.txt
  remote_mark "21-install-done" "rc=$install_rc"
  if [[ "$install_rc" != "0" ]]; then
    rc=$install_rc
  else
    remote_mark "30-producer-start" "mode=$mode"
    python3 stock_runner_entry.py --mode "$mode" >diagnostics/runner1_last_stdout.txt 2>diagnostics/runner1_last_stderr.txt
    rc=$?
    remote_mark "31-producer-done" "rc=$rc"
  fi
  set -e
fi

printf '%s\n' "$rc" > diagnostics/runner1_last_exit_code.txt
printf '%s\n' "$(date -u +%FT%TZ)" > diagnostics/runner1_finished_utc.txt

remote_mark "40-persist-start" "rc=$rc"
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
    remote_mark "49-persist-failed" "rc=91"
    echo "Failed to persist runner-1 state" >&2
    exit 91
  fi
fi
remote_mark "50-complete" "rc=$rc"

exit "$rc"
