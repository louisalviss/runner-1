# Wave Rider Stock — VPS preclose fallback

Purpose: protect canonical runner-1/o/1 Stage-1 preclose from delayed GitHub schedule execution.

- Existing GitHub Action x.yml remains primary; the fallback does not disable it.
- VPS timer fires 15:47 and 15:52 New York time, Monday–Friday, DST-aware.
- NYSE market calendar must show an open regular session; holidays and early close are blocked. No late catch-up (Persistent=false).
- If a final preclose slot for the current NY session already exists, do nothing.
- Otherwise clone latest runner-1 main into a disposable directory, run the same stock_ci.sh with EVENT_NAME=workflow_dispatch and INPUT_M=3, and require updated o/1 to match remote Git HEAD after push.
- Python environment uses a dedicated venv; no credentials in repository or script.
- Status: /var/lib/wr-preclose-fallback/last-status.json; diagnostic logs: journalctl -u wr-preclose-fallback.service.
- Lock ensures no two VPS invocations collide; two attempts provide redundancy.
- If upstream Stage-1 or GitHub push fails, fail closed; do not invent a preclose snapshot.
- --self-test verifies imports and GitHub access without mutating data.

Install:
  python3 -m venv /opt/wr-preclose-fallback/venv
  /opt/wr-preclose-fallback/venv/bin/python3 -m pip install -r requirements.txt
  install -m 0755 ops/wr-preclose-fallback.sh /usr/local/libexec/wr-preclose-fallback
  install -m 0644 ops/wr-preclose-fallback.service /etc/systemd/system/
  install -m 0644 ops/wr-preclose-fallback.timer /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now wr-preclose-fallback.timer
  systemctl start wr-preclose-fallback.service   # outside window is a safe skip

Check:
  /usr/local/libexec/wr-preclose-fallback --self-test
  systemctl show wr-preclose-fallback.timer -p ActiveState -p NextElapseUSecRealtime
  journalctl -u wr-preclose-fallback.service -n 40 --no-pager

End-to-end remains pending first valid NYSE session where slot o/1 is current and verified.
Rollback: systemctl disable --now wr-preclose-fallback.timer; does not delete data.
