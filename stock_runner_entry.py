#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from typing import Any
from zoneinfo import ZoneInfo

import stock_runner as runner

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def fetch_all_recoverable(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, float]]], dict[str, str]]:
    """Fetch every Stage-1 symbol and report only residual failures after retries."""
    wanted = [(str(r["exchange"]), str(r["symbol"])) for r in rows]
    got: dict[str, list[dict[str, float]]] = {}
    last_error: dict[str, str] = {}

    for i in range(0, len(wanted), 16):
        batch = wanted[i : i + 16]
        try:
            part = runner.fetch_3m_batch(batch)
            got.update(part)
            for _, symbol in batch:
                if symbol in part:
                    last_error.pop(symbol, None)
                else:
                    last_error[symbol] = "no 3m bars returned"
        except Exception as exc:
            for _, symbol in batch:
                last_error[symbol] = str(exc)
        time.sleep(0.2)

    missing = [(exchange, symbol) for exchange, symbol in wanted if symbol not in got]
    for _attempt in range(2):
        if not missing:
            break
        next_missing: list[tuple[str, str]] = []
        for i in range(0, len(missing), 6):
            batch = missing[i : i + 6]
            try:
                part = runner.fetch_3m_batch(batch, timeout=24.0)
                got.update(part)
                for exchange, symbol in batch:
                    if symbol in part:
                        last_error.pop(symbol, None)
                    else:
                        last_error[symbol] = "no 3m bars returned"
                        next_missing.append((exchange, symbol))
            except Exception as exc:
                for exchange, symbol in batch:
                    last_error[symbol] = str(exc)
                    next_missing.append((exchange, symbol))
            time.sleep(0.4)
        missing = next_missing

    residual = {
        symbol: last_error.get(symbol, "no 3m bars returned after retries")
        for _, symbol in wanted
        if symbol not in got
    }
    return got, residual


def canonical_auto_mode() -> str:
    """Resolve backup/manual auto without ever firing a checkpoint early.

    The Cloudflare backup intentionally watches broad windows. This guard makes
    the public Stock producer authoritative on the methodology times:
    Main starts at 10:30 ET (one hour after the 09:30 open) and may retry until
    11:15 ET; Mid starts at 12:45 ET; Preclose starts at 15:45 ET; Smoothness
    runs at 05:15 VN post-close. Backup dispatches before those targets cannot
    mutate canonical state early.
    """
    now = runner.now_utc()
    if runner.market_open_now(now):
        et = now.astimezone(runner.NY)
        minute = et.hour * 60 + et.minute
        if 10 * 60 + 30 <= minute <= 11 * 60 + 15:
            return "main"
        if 12 * 60 + 45 <= minute <= 12 * 60 + 57:
            return "mid"
        if 15 * 60 + 45 <= minute <= 15 * 60 + 57:
            return "preclose"
        return "noop"

    vn = now.astimezone(VN)
    minute = vn.hour * 60 + vn.minute
    # Tuesday-Saturday VN correspond to the preceding Mon-Fri US sessions.
    if vn.weekday() in {1, 2, 3, 4, 5} and 5 * 60 + 15 <= minute <= 5 * 60 + 27:
        return "smoothness"
    return "noop"


runner.fetch_all = fetch_all_recoverable

if __name__ == "__main__":
    # stock_ci.sh passes --mode auto for Cloudflare backup/manual auto dispatch.
    # Resolve it here so broad backup windows can never mutate canonical state early.
    requested_auto = "--mode" not in sys.argv or any(
        sys.argv[i] == "--mode" and i + 1 < len(sys.argv) and sys.argv[i + 1] == "auto"
        for i in range(len(sys.argv))
    )
    if requested_auto:
        resolved = canonical_auto_mode()
        if resolved == "noop":
            print(json.dumps({"mode": "noop", "action": "outside-canonical-window"}, indent=2))
            raise SystemExit(0)
        if "--mode" in sys.argv:
            idx = sys.argv.index("--mode")
            if idx + 1 < len(sys.argv):
                sys.argv[idx + 1] = resolved
        else:
            sys.argv.extend(["--mode", resolved])

    raise SystemExit(runner.main())
