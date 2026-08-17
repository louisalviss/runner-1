#!/usr/bin/env python3
from __future__ import annotations

import time
from typing import Any

import stock_runner as runner


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


runner.fetch_all = fetch_all_recoverable

if __name__ == "__main__":
    raise SystemExit(runner.main())
