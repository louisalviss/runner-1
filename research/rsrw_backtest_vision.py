#!/usr/bin/env python3
import csv
import io
import inspect
import time
import zipfile
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import rsrw_backtest as bt

BASE = "https://data.binance.vision/data/futures/um"
# Last fully completed UTC day when this research run was launched.
bt.TEST_END = 1787270400000  # 2026-08-21 00:00 UTC


def fetch_bytes(url, retries=4):
    last = None
    for k in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "wr-rsrw-research/1.2"})
            with urlopen(req, timeout=30) as r:
                return r.read()
        except HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except Exception as e:
            last = e
        time.sleep(1.0 * (k + 1))
    raise last


def parse_zip(blob):
    rows = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        text = z.read(name).decode("utf-8-sig")
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].lstrip("-").isdigit():
            continue
        t = int(row[0])
        if t > 10**14:
            t //= 1000
        rows.append({"t": t, "o": float(row[1]), "h": float(row[2]), "l": float(row[3]), "c": float(row[4])})
    return rows


def fetch_klines(symbol, start_ms, end_ms):
    rows = []
    monthly = f"{BASE}/monthly/klines/{symbol}/5m/{symbol}-5m-2026-07.zip"
    blob = fetch_bytes(monthly)
    if blob:
        rows.extend(parse_zip(blob))

    d = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
    while d < end_dt:
        ds = d.strftime("%Y-%m-%d")
        daily = f"{BASE}/daily/klines/{symbol}/5m/{symbol}-5m-{ds}.zip"
        blob = fetch_bytes(daily)
        if blob:
            rows.extend(parse_zip(blob))
        d += timedelta(days=1)

    by_t = {r["t"]: r for r in rows if start_ms <= r["t"] < end_ms}
    return [by_t[t] for t in sorted(by_t)]


def exchange_ticks():
    return {
        "BNBUSDT": 0.01,
        "TRXUSDT": 0.00001,
        "SOLUSDT": 0.01,
        "ETHUSDT": 0.01,
        "XRPUSDT": 0.0001,
        "BTCUSDT": 0.1,
    }


# The verified v2.5.15 Python oracle is tick-aware. Reproduce that precision
# rule here so decimal binary noise cannot turn equality into a missed fill.
src = inspect.getsource(bt.simulate)
src = src.replace("H[i]>=pending['entry']", "H[i] >= pending['entry'] - tick*1e-6")
src = src.replace("L[i]<=pending['entry']", "L[i] <= pending['entry'] + tick*1e-6")
ns = dict(bt.__dict__)
exec(src, ns)
bt.simulate = ns['simulate']

bt.fetch_klines = fetch_klines
bt.exchange_ticks = exchange_ticks
bt.main()
