#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import math
import random
import re
import string
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal
import requests
import websocket

NY = ZoneInfo("America/New_York")
TV_SCAN_URL = "https://scanner.tradingview.com/america/scan"
TV_WS_URL = "wss://data.tradingview.com/socket.io/websocket?from=chart%2F"
TV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Content-Type": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/markets/stocks-usa/stock-screener/",
}
OUT = Path("o")
DIAG = Path("diagnostics")
_KEY_MASK = 0xA7
_KEY_OBF = [0x3D, 0x90, 0x63, 0x46, 0xCA, 0xF5, 0x1F, 0xA8, 0xE6, 0x74, 0x3B, 0x80, 0xCD, 0x56, 0xA2, 0x19]
KEY = bytes(x ^ _KEY_MASK for x in _KEY_OBF)
ADV30_MIN = 400_000_000.0
MARKET_CAP_MIN = 170_000_000_000.0
SMOOTH = {
    "anomaly_max_pct": 4.0,
    "median_gap_to_range_max": 0.18,
    "median_dollar_volume_3m_min": 100_000.0,
}
_FRAME_RE = re.compile(r"~m~(\d+)~m~")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def encode_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    packed = zlib.compress(raw, 9)
    xored = bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(packed))
    return base64.b85encode(xored).decode() + "\n"


def decode_payload(text: str) -> dict[str, Any]:
    packed = base64.b85decode(text.strip().encode())
    raw = bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(packed))
    return json.loads(zlib.decompress(raw))


def read_slot(slot: str) -> dict[str, Any] | None:
    path = OUT / slot
    if not path.exists():
        return None
    try:
        return decode_payload(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_slot(slot: str, payload: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / slot).write_text(encode_payload(payload), encoding="utf-8")


def write_diag(name: str, payload: dict[str, Any]) -> None:
    DIAG.mkdir(parents=True, exist_ok=True)
    (DIAG / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tv_payload() -> dict[str, Any]:
    return {
        "options": {"lang": "en"},
        "markets": ["america"],
        "symbols": {"query": {"types": ["stock", "dr"]}, "tickers": []},
        "filter": [{"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE"]}],
        "columns": [
            "name", "description", "exchange", "type", "typespecs", "close", "volume",
            "Value.Traded", "average_volume_30d_calc", "market_cap_basic",
        ],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 10000],
    }


def as_float(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def scan_stage1() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    r = requests.post(TV_SCAN_URL, headers=TV_HEADERS, json=tv_payload(), timeout=30)
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("TradingView response missing data[]")
    total = int(payload.get("totalCount", len(data)) or len(data))
    if total > 10000:
        raise RuntimeError(f"TradingView totalCount={total} exceeds range=10000")

    parsed = 0
    exchange_counts = {"NASDAQ": 0, "NYSE": 0}
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        d = item.get("d")
        if not isinstance(d, list) or len(d) < 10:
            continue
        name, desc, exchange, typ, typespecs, close, volume, turnover, avg30, cap = d[:10]
        if typ not in {"stock", "dr"} or exchange not in exchange_counts:
            continue
        close_f, vol_f, turn_f, avg_f, cap_f = map(as_float, (close, volume, turnover, avg30, cap))
        if close_f is None or close_f <= 0 or vol_f is None or vol_f < 0:
            continue
        parsed += 1
        exchange_counts[str(exchange)] += 1
        adv30 = close_f * avg_f if avg_f is not None and avg_f >= 0 else None
        pass_adv = adv30 is not None and adv30 > ADV30_MIN
        pass_cap = cap_f is not None and cap_f > MARKET_CAP_MIN
        if not (pass_adv or pass_cap):
            continue
        rows.append({
            "symbol": str(name),
            "exchange": str(exchange),
            "description": str(desc or ""),
            "price": close_f,
            "volume": int(vol_f),
            "turnover_usd": turn_f,
            "average_volume_30d": avg_f,
            "avg_dollar_volume_30d_usd": adv30,
            "market_cap_usd": cap_f,
            "qualified_by": "ADV30+MARKET_CAP" if pass_adv and pass_cap else ("ADV30" if pass_adv else "MARKET_CAP_RESCUE"),
        })
    if parsed < 2500 or exchange_counts["NASDAQ"] < 1200 or exchange_counts["NYSE"] < 900:
        raise RuntimeError(f"incomplete TradingView universe parsed={parsed} exchanges={exchange_counts}")
    if len(rows) < 100:
        raise RuntimeError(f"implausibly small Stage-1 result count={len(rows)}")
    rows.sort(key=lambda x: x["symbol"])
    return rows, {"total_count": total, "parsed_count": parsed, "exchange_counts": exchange_counts}


def market_open_now(dt: datetime) -> bool:
    et = dt.astimezone(NY)
    cal = mcal.get_calendar("NYSE")
    sched = cal.schedule(start_date=et.date().isoformat(), end_date=et.date().isoformat())
    if sched.empty:
        return False
    row = sched.iloc[0]
    op = row["market_open"].to_pydatetime().astimezone(timezone.utc)
    cl = row["market_close"].to_pydatetime().astimezone(timezone.utc)
    return op <= dt < cl


def auto_mode(dt: datetime) -> str:
    et = dt.astimezone(NY)
    minute = et.hour * 60 + et.minute
    if market_open_now(dt):
        if minute < 11 * 60 + 30:
            return "main"
        if minute < 14 * 60 + 30:
            return "mid"
        return "preclose"
    return "smoothness"


def new_live(session: str) -> dict[str, Any]:
    return {
        "schema_version": "stock-runner-1.0",
        "session_date_us": session,
        "updated_at_utc": None,
        "latest_mode": None,
        "latest_scan_status": None,
        "latest_successful_scan_at_utc": None,
        "union_count": 0,
        "active_count": 0,
        "rows": [],
        "checkpoints": [],
        "finalized": False,
        "finalized_by_mode": None,
    }


def checkpoint_complete(live: dict[str, Any], mode: str) -> bool:
    return any(
        x.get("mode") == mode and x.get("status") == "COMPLETE"
        for x in live.get("checkpoints", [])
        if isinstance(x, dict)
    )


def run_checkpoint(mode: str) -> dict[str, Any]:
    dt = now_utc()
    if not market_open_now(dt):
        raise RuntimeError(f"{mode} requested while NYSE regular session is closed")
    session = dt.astimezone(NY).date().isoformat()
    live = read_slot("0")
    if not live or live.get("session_date_us") != session:
        live = new_live(session)
    if checkpoint_complete(live, mode):
        return {
            "action": "already-complete",
            "mode": mode,
            "session_date_us": session,
            "union_count": live.get("union_count"),
        }

    ts = dt.isoformat()
    try:
        scan_rows, diag = scan_stage1()
        status = "COMPLETE"
        error = None
    except Exception as exc:
        scan_rows, diag, status, error = [], {}, "FAILED", str(exc)

    cp = {
        "mode": mode,
        "generated_at_utc": ts,
        "status": status,
        "qualified_count": len(scan_rows),
        "error": error,
    }
    live["updated_at_utc"] = ts
    live["latest_mode"] = mode
    live["latest_scan_status"] = status
    live.setdefault("checkpoints", []).append(cp)
    live["checkpoints"] = live["checkpoints"][-24:]

    if status == "COMPLETE":
        existing = {
            str(r["symbol"]): r
            for r in live.get("rows", [])
            if isinstance(r, dict) and r.get("symbol")
        }
        for r in existing.values():
            r["active_now"] = False
        for src in scan_rows:
            sym = src["symbol"]
            cur = existing.get(sym)
            if cur is None:
                cur = {
                    "symbol": sym,
                    "exchange": src["exchange"],
                    "first_qualified_at_utc": ts,
                    "last_qualified_at_utc": ts,
                    "scan_count": 1,
                    "active_now": True,
                }
                existing[sym] = cur
            else:
                cur["exchange"] = src["exchange"]
                cur["last_qualified_at_utc"] = ts
                cur["scan_count"] = int(cur.get("scan_count", 0)) + 1
                cur["active_now"] = True
        live["rows"] = sorted(existing.values(), key=lambda x: x["symbol"])
        live["union_count"] = len(live["rows"])
        live["active_count"] = sum(1 for r in live["rows"] if r.get("active_now"))
        live["latest_successful_scan_at_utc"] = ts
        live["latest_scan_diagnostics"] = diag
        if mode == "preclose":
            live["finalized"] = True
            live["finalized_by_mode"] = "preclose"
    write_slot("0", live)
    if mode == "preclose" and status == "COMPLETE":
        write_slot("1", live)
    if status != "COMPLETE":
        write_diag("runner1_stage1_failure.json", {"at": ts, "mode": mode, "error": error})
        raise RuntimeError(error or "Stage-1 failed")
    return {
        "action": "published",
        "mode": mode,
        "session_date_us": session,
        "qualified_count": len(scan_rows),
        "union_count": live["union_count"],
    }


def frame(payload: str) -> str:
    return f"~m~{len(payload)}~m~{payload}"


def command(method: str, params: list[Any]) -> str:
    return frame(json.dumps({"m": method, "p": params}, separators=(",", ":")))


def session_id(prefix: str) -> str:
    return prefix + "".join(random.choice(string.ascii_lowercase) for _ in range(12))


def iter_payloads(raw: str) -> Iterable[str]:
    pos = 0
    while pos < len(raw):
        m = _FRAME_RE.match(raw, pos)
        if m is None:
            break
        n = int(m.group(1))
        start = m.end()
        end = start + n
        if end > len(raw):
            break
        yield raw[start:end]
        pos = end


def parse_series(series: dict[str, Any]) -> list[dict[str, float]]:
    out = []
    samples = series.get("s") if isinstance(series, dict) else None
    if not isinstance(samples, list):
        return out
    for sample in samples:
        v = sample.get("v") if isinstance(sample, dict) else None
        if not isinstance(v, list) or len(v) < 6:
            continue
        try:
            ts, o, h, l, c, vol = map(float, v[:6])
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in (ts, o, h, l, c, vol)) or min(o, h, l, c) <= 0 or vol < 0:
            continue
        out.append({"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": vol})
    return sorted(out, key=lambda x: x["timestamp"])


def fetch_3m_batch(
    symbols: list[tuple[str, str]],
    bars: int = 150,
    timeout: float = 22.0,
) -> dict[str, list[dict[str, float]]]:
    if not symbols:
        return {}
    cs = session_id("cs_")
    ws = websocket.create_connection(
        TV_WS_URL,
        timeout=timeout,
        origin="https://www.tradingview.com",
        header=["User-Agent: Mozilla/5.0"],
    )
    series_to_symbol: dict[str, str] = {}
    collected: dict[str, list[dict[str, float]]] = {}
    try:
        ws.send(command("set_auth_token", ["unauthorized_user_token"]))
        ws.send(command("chart_create_session", [cs, ""]))
        for i, (exchange, symbol) in enumerate(symbols):
            alias, sid = f"sym_{i}", f"ser_{i}"
            series_to_symbol[sid] = symbol
            spec = {
                "symbol": f"{exchange.upper()}:{symbol}",
                "adjustment": "splits",
                "session": "regular",
            }
            ws.send(command("resolve_symbol", [cs, alias, "=" + json.dumps(spec, separators=(",", ":"))]))
            ws.send(command("create_series", [cs, sid, sid, alias, "3", int(bars)]))
        pending = set(series_to_symbol)
        deadline = time.monotonic() + timeout
        while pending and time.monotonic() < deadline:
            ws.settimeout(max(0.5, deadline - time.monotonic()))
            try:
                raw = ws.recv()
            except Exception:
                break
            if not isinstance(raw, str):
                continue
            for p in iter_payloads(raw):
                if p.startswith("~h~"):
                    ws.send(frame(p))
                    continue
                try:
                    msg = json.loads(p)
                except json.JSONDecodeError:
                    continue
                method, params = msg.get("m"), msg.get("p")
                if (
                    method == "timescale_update"
                    and isinstance(params, list)
                    and len(params) >= 2
                    and isinstance(params[1], dict)
                ):
                    for sid, series in params[1].items():
                        if sid in series_to_symbol and isinstance(series, dict):
                            parsed = parse_series(series)
                            if parsed:
                                collected[series_to_symbol[sid]] = parsed
                elif method == "series_completed" and isinstance(params, list) and len(params) >= 2:
                    pending.discard(str(params[1]))
        return collected
    finally:
        try:
            ws.close()
        except Exception:
            pass


def latest_regular_session(
    bars: list[dict[str, float]],
) -> tuple[str | None, list[dict[str, float]]]:
    by_date: dict[str, list[dict[str, float]]] = {}
    for b in bars:
        dt = datetime.fromtimestamp(b["timestamp"], tz=NY)
        minute = dt.hour * 60 + dt.minute
        if 570 <= minute < 960:
            by_date.setdefault(dt.date().isoformat(), []).append(b)
    if not by_date:
        return None, []
    d = max(by_date)
    return d, sorted(by_date[d], key=lambda x: x["timestamp"])


def quality_metrics(bars: list[dict[str, float]]) -> dict[str, Any]:
    session_date, s = latest_regular_session(bars)
    if len(s) < 2:
        return {
            "session_date_us": session_date,
            "bar_count": len(s),
            "missing_bar_pct": None,
            "zero_range_pct": None,
            "tiny_bar_pct": None,
            "gap_bar_pct": None,
            "median_gap_to_range": None,
            "median_dollar_volume_3m": None,
        }
    ranges = [max(0.0, b["high"] - b["low"]) for b in s]
    dvs = [b["close"] * b["volume"] for b in s]
    pos = [x for x in ranges if x > 0]
    med = median(pos) if pos else 0.0
    expected = int(round((s[-1]["timestamp"] - s[0]["timestamp"]) / 180.0)) + 1
    expected = max(expected, len(s))
    gaps = []
    flags = 0
    for prev, cur in zip(s, s[1:]):
        denom = med if med > 0 else max(prev["close"] * 1e-6, 1e-12)
        ratio = abs(cur["open"] - prev["close"]) / denom
        gaps.append(ratio)
        if ratio >= 0.75:
            flags += 1
    return {
        "session_date_us": session_date,
        "bar_count": len(s),
        "missing_bar_pct": round(max(0.0, 1.0 - len(s) / expected) * 100, 3),
        "zero_range_pct": round(sum(1 for x in ranges if x <= 1e-12) / len(s) * 100, 3),
        "tiny_bar_pct": round(
            (sum(1 for x in ranges if x <= med * 0.20) / len(s) if med > 0 else 1.0) * 100,
            3,
        ),
        "gap_bar_pct": round(flags / max(1, len(s) - 1) * 100, 3),
        "median_gap_to_range": round(median(gaps) if gaps else 0.0, 4),
        "median_dollar_volume_3m": round(median(dvs) if dvs else 0.0, 2),
    }


def classify(metrics: dict[str, Any]) -> tuple[str, float | None, str]:
    req = [
        "missing_bar_pct",
        "zero_range_pct",
        "tiny_bar_pct",
        "gap_bar_pct",
        "median_gap_to_range",
        "median_dollar_volume_3m",
    ]
    if any(metrics.get(k) is None for k in req):
        return "INSUFFICIENT", None, "missing required 3m metrics"
    anomaly = sum(float(metrics[k]) for k in req[:4])
    failures = []
    if anomaly > SMOOTH["anomaly_max_pct"]:
        failures.append("anomaly>4.0%")
    if float(metrics["median_gap_to_range"]) > SMOOTH["median_gap_to_range_max"]:
        failures.append("median_gap/range>0.18")
    if float(metrics["median_dollar_volume_3m"]) < SMOOTH["median_dollar_volume_3m_min"]:
        failures.append("median_$vol3m<100000")
    return (
        "CLEAN" if not failures else "NON_CLEAN",
        round(anomaly, 3),
        "pass all v0 gates" if not failures else "; ".join(failures),
    )


def fetch_all(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, float]]], dict[str, str]]:
    wanted = [(str(r["exchange"]), str(r["symbol"])) for r in rows]
    got: dict[str, list[dict[str, float]]] = {}
    errors: dict[str, str] = {}
    batch_size = 16
    for i in range(0, len(wanted), batch_size):
        batch = wanted[i : i + batch_size]
        try:
            got.update(fetch_3m_batch(batch))
        except Exception as exc:
            for _, s in batch:
                errors[s] = str(exc)
        time.sleep(0.2)
    missing = [(e, s) for e, s in wanted if s not in got]
    for _attempt in range(2):
        if not missing:
            break
        next_missing = []
        for i in range(0, len(missing), 6):
            batch = missing[i : i + 6]
            try:
                part = fetch_3m_batch(batch, timeout=24.0)
                got.update(part)
                for exchange, symbol in batch:
                    if symbol not in part:
                        next_missing.append((exchange, symbol))
            except Exception as exc:
                for exchange, symbol in batch:
                    errors[symbol] = str(exc)
                    next_missing.append((exchange, symbol))
            time.sleep(0.4)
        missing = next_missing
    for _, s in missing:
        errors.setdefault(s, "no 3m bars returned after retries")
    return got, errors


def run_smoothness() -> dict[str, Any]:
    final = read_slot("1")
    if not final or not final.get("finalized") or final.get("finalized_by_mode") != "preclose":
        return {"action": "waiting-for-final"}
    session = str(final.get("session_date_us") or "")
    existing = read_slot("2")
    if (
        existing
        and existing.get("session_date_us") == session
        and int(existing.get("failure_count", 1)) == 0
        and int(existing.get("insufficient_count", 1)) == 0
    ):
        return {
            "action": "already-complete",
            "session_date_us": session,
            "clean_count": existing.get("clean_count"),
        }
    rows = final.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("final Stage-1 rows empty")
    started = now_utc()
    bars_by_symbol, fetch_errors = fetch_all(rows)
    out_rows = []
    failures: dict[str, str] = dict(fetch_errors)
    insufficient = []
    wrong_session: dict[str, str | None] = {}
    for src in rows:
        sym = str(src["symbol"])
        metrics = quality_metrics(bars_by_symbol.get(sym, []))
        if metrics.get("session_date_us") != session:
            wrong_session[sym] = metrics.get("session_date_us")
        label, anomaly, reason = classify(metrics)
        if label == "INSUFFICIENT":
            insufficient.append(sym)
        out_rows.append({
            "symbol": sym,
            "exchange": str(src.get("exchange") or ""),
            **metrics,
            "anomaly_pct": anomaly,
            "smoothness_v0": label,
            "smoothness_reason": reason,
        })
    if failures or wrong_session or insufficient:
        diag = {
            "at": started.isoformat(),
            "session_date_us": session,
            "fetch_failures": failures,
            "wrong_session": wrong_session,
            "insufficient": sorted(insufficient),
            "stage1_union_count": len(rows),
        }
        write_diag("runner1_smoothness_failure.json", diag)
        raise RuntimeError(
            f"smoothness incomplete failures={len(failures)} "
            f"wrong_session={len(wrong_session)} insufficient={len(insufficient)}"
        )
    payload = {
        "schema_version": "stock-smoothness-1.0",
        "session_date_us": session,
        "generated_utc": started.isoformat(),
        "rule_name": "Smoothness v0",
        "rule": SMOOTH,
        "stage1_union_count": len(rows),
        "clean_count": sum(1 for r in out_rows if r["smoothness_v0"] == "CLEAN"),
        "failure_count": 0,
        "insufficient_count": 0,
        "rows": sorted(out_rows, key=lambda x: x["symbol"]),
    }
    write_slot("2", payload)
    return {
        "action": "published",
        "session_date_us": session,
        "stage1_union_count": len(rows),
        "clean_count": payload["clean_count"],
    }


def run_probe() -> dict[str, Any]:
    started = now_utc()
    rows, diag = scan_stage1()
    sample = next((r for r in rows if r["symbol"] == "MSFT"), rows[0])
    bars = fetch_3m_batch(
        [(sample["exchange"], sample["symbol"])],
        bars=150,
        timeout=25.0,
    ).get(sample["symbol"], [])
    metrics = quality_metrics(bars)
    if not bars:
        raise RuntimeError("TradingView websocket probe returned no bars")
    payload = {
        "ok": True,
        "generated_utc": started.isoformat(),
        "stage1_qualified_count": len(rows),
        "stage1_diagnostics": diag,
        "bar_probe_symbol": sample["symbol"],
        "bar_probe_count": len(bars),
        "bar_probe_session_date_us": metrics.get("session_date_us"),
    }
    write_diag("runner1_probe.json", payload)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--mode",
        choices=["auto", "main", "mid", "preclose", "smoothness", "probe"],
        default="auto",
    )
    args = ap.parse_args()
    mode = auto_mode(now_utc()) if args.mode == "auto" else args.mode
    if mode == "probe":
        result = run_probe()
    elif mode == "smoothness":
        result = run_smoothness()
    else:
        result = run_checkpoint(mode)
    print(json.dumps({"mode": mode, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
