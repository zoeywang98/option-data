#!/usr/bin/env python3
"""Normalize an authorized index/equity intraday capture into the option-data contract."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from option_data.contract import CSV_COLUMNS


ET = ZoneInfo("America/New_York")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS[path.name])
        writer.writeheader()
        writer.writerows(rows)


def blank_row(filename: str) -> dict[str, object]:
    return {column: "" for column in CSV_COLUMNS[filename]}


def iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(ET).isoformat()


def vendor_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ET).isoformat()


def rolling_realized_vol(closes: list[float], window: int = 30) -> float | None:
    if len(closes) < 3:
        return None
    returns = [math.log(b / a) for a, b in zip(closes[-window - 1 : -1], closes[-window:]) if a > 0 and b > 0]
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * math.sqrt(252 * 390) * 100


def normalize_underlying(
    raw: dict, run_at: datetime, previous_close: float, symbol: str
) -> tuple[list[dict[str, object]], float, float | None]:
    cutoff_ms = int(run_at.timestamp() * 1000)
    records: list[tuple[datetime, dict]] = []
    for raw_ms, values in raw.get("data", {}).items():
        stamp = datetime.fromtimestamp(int(raw_ms) / 1000, tz=timezone.utc).astimezone(ET)
        if stamp.date() == run_at.date() and time(9, 30) <= stamp.time() <= run_at.time() and int(raw_ms) <= cutoff_ms:
            records.append((stamp, values))
    records.sort(key=lambda item: item[0])
    if not records:
        raise ValueError(f"QuantData {symbol} price response had no regular-session rows before run time")

    rows: list[dict[str, object]] = []
    day_open = float(records[0][1]["openPrice"])
    cumulative_high = float("-inf")
    cumulative_low = float("inf")
    closes: list[float] = []
    true_ranges: list[float] = []
    prior_high: float | None = None
    prior_low: float | None = None
    prior_velocity: float | None = None
    prior_close = previous_close

    for stamp, values in records:
        open_price = float(values["openPrice"])
        high = float(values["highPrice"])
        low = float(values["lowPrice"])
        close = float(values["closePrice"])
        old_high, old_low = cumulative_high, cumulative_low
        cumulative_high = max(cumulative_high, high)
        cumulative_low = min(cumulative_low, low)
        true_ranges.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
        closes.append(close)
        velocity = close - prior_close
        realized = rolling_realized_vol(closes)

        row = blank_row("underlying_1m.csv")
        row.update({
            "timestamp": stamp.isoformat(),
            "symbol": symbol,
            "session": "regular",
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "previous_close": previous_close,
            "day_open": day_open,
            "day_high": cumulative_high,
            "day_low": cumulative_low,
            "is_new_high": str(high > old_high).lower(),
            "is_new_low": str(low < old_low).lower(),
            "higher_high": "" if prior_high is None else str(high > prior_high).lower(),
            "lower_low": "" if prior_low is None else str(low < prior_low).lower(),
            "atr": round(sum(true_ranges[-14:]) / min(14, len(true_ranges)), 6),
            "realized_vol": "" if realized is None else round(realized, 6),
            "price_velocity": round(velocity, 6),
            "price_acceleration": "" if prior_velocity is None else round(velocity - prior_velocity, 6),
            "source": "QuantData stock-price-over-time",
        })
        rows.append(row)
        prior_high, prior_low, prior_close, prior_velocity = high, low, close, velocity

    return rows, float(rows[-1]["close"]), rolling_realized_vol(closes)


def nearest_delta(points: list[dict], call_put: str, target: float) -> float | None:
    candidates = [point for point in points if point["call_put"] == call_put and point["delta"] != ""]
    if not candidates:
        return None
    return float(min(candidates, key=lambda point: abs(float(point["delta"]) - target))["iv"])


def normalize_iv_surface(
    raw: dict, run_at: datetime, realized_vol: float | None
) -> tuple[list[dict[str, object]], float | None]:
    spot = float(raw["stockPrice"])
    points_by_expiry: dict[str, list[dict]] = {}
    for expiration, strikes in raw.get("data", {}).items():
        points: list[dict] = []
        for raw_strike, sides in strikes.items():
            strike = float(raw_strike)
            for vendor_side, call_put in (("CALL", "call"), ("PUT", "put")):
                cell = sides.get(vendor_side)
                if not isinstance(cell, dict):
                    continue
                points.append({
                    "strike": strike,
                    "call_put": call_put,
                    "delta": cell.get("delta", ""),
                    "iv": cell.get("iv", ""),
                })
        points_by_expiry[expiration] = points

    summaries: dict[str, dict[str, float | None]] = {}
    for expiration, points in points_by_expiry.items():
        if not points:
            continue
        nearest_strike = min({float(point["strike"]) for point in points}, key=lambda strike: abs(strike - spot))
        atm_values = [float(point["iv"]) for point in points if float(point["strike"]) == nearest_strike and point["iv"] != ""]
        atm = sum(atm_values) / len(atm_values) if atm_values else None
        put10 = nearest_delta(points, "put", -0.10)
        put25 = nearest_delta(points, "put", -0.25)
        call25 = nearest_delta(points, "call", 0.25)
        call10 = nearest_delta(points, "call", 0.10)
        summaries[expiration] = {
            "atm": atm,
            "put10": put10,
            "put25": put25,
            "call25": call25,
            "call10": call10,
            "rr25": None if put25 is None or call25 is None else call25 - put25,
            "bf25": None if atm is None or put25 is None or call25 is None else (put25 + call25) / 2 - atm,
        }

    ordered_expiries = sorted(summaries)
    front_iv = summaries[ordered_expiries[0]]["atm"] if ordered_expiries else None
    thirty = run_at.date().toordinal() + 30
    back_expiry = min(ordered_expiries, key=lambda value: abs(date.fromisoformat(value).toordinal() - thirty)) if ordered_expiries else None
    back_iv = summaries[back_expiry]["atm"] if back_expiry else None
    term_slope = None if front_iv is None or back_iv is None else back_iv - front_iv

    rows: list[dict[str, object]] = []
    for expiration in sorted(points_by_expiry):
        dte = max(0, (date.fromisoformat(expiration) - run_at.date()).days)
        summary = summaries.get(expiration, {})
        for point in sorted(points_by_expiry[expiration], key=lambda value: (value["strike"], value["call_put"])):
            row = blank_row("iv_surface.csv")
            row.update({
                "timestamp": run_at.isoformat(),
                "expiration": expiration,
                "dte": dte,
                "strike": point["strike"],
                "call_put": point["call_put"],
                "delta": point["delta"],
                "iv": point["iv"],
                "underlying_price": spot,
                "coordinate_type": "fixed_strike",
                "atm_iv": summary.get("atm", ""),
                "10_delta_put_iv": summary.get("put10", ""),
                "25_delta_put_iv": summary.get("put25", ""),
                "25_delta_call_iv": summary.get("call25", ""),
                "10_delta_call_iv": summary.get("call10", ""),
                "rr25": summary.get("rr25", ""),
                "bf25": summary.get("bf25", ""),
                "front_iv": front_iv if front_iv is not None else "",
                "back_iv": back_iv if back_iv is not None else "",
                "term_slope": term_slope if term_slope is not None else "",
                "realized_vol": realized_vol if realized_vol is not None else "",
                "iv_minus_rv": "" if summary.get("atm") is None or realized_vol is None else float(summary["atm"]) - realized_vol,
                "source": "QuantData term-structure",
            })
            rows.append(row)
    return rows, front_iv


def normalize_flow(raw: dict, run_at: datetime) -> list[dict[str, object]]:
    cutoff_ms = int(run_at.timestamp() * 1000)
    rows: list[dict[str, object]] = []
    for item in sorted(raw.get("data", []), key=lambda value: int(value.get("tradeTime", 0))):
        if int(item.get("tradeTime", 0)) > cutoff_ms:
            continue
        side = str(item.get("tradeSideCode", "")).upper()
        consolidation = str(item.get("tradeConsolidationType", "")).upper()
        trade_type = str(item.get("tradeType", ""))
        greeks = item.get("greeks") or {}
        row = blank_row("option_flow.csv")
        row.update({
            "timestamp": iso_from_ms(int(item["tradeTime"])),
            "option_symbol": item.get("osi", ""),
            "underlying": item.get("ticker", "SPX"),
            "expiration": item.get("expirationDate", ""),
            "dte": item.get("dte", ""),
            "strike": item.get("strikePrice", ""),
            "call_put": str(item.get("contractType", "")).lower(),
            "trade_price": item.get("optionPrice", ""),
            "bid": item.get("bidPrice", ""),
            "ask": item.get("askPrice", ""),
            "trade_size": item.get("size", ""),
            "premium": item.get("premium", ""),
            "volume": item.get("volume", ""),
            "open_interest": item.get("openInterest", ""),
            "underlying_price": item.get("stockPrice", ""),
            "iv": item.get("impliedVolatility", ""),
            "delta": greeks.get("delta", ""),
            "exchange": item.get("exchange", ""),
            "trade_condition": trade_type,
            "sweep": str("SWEEP" in consolidation or bool(item.get("isGoldenSweep"))).lower(),
            "block": str("BLOCK" in consolidation).lower(),
            "multi_leg": str("MULTI" in trade_type.upper()).lower(),
            "aggressor_side": {"ASK": "ask", "BID": "bid", "MID_MARKET": "mid"}.get(side, "unknown"),
            "opening_closing_inference": "opening" if item.get("isOpeningPosition") is True else "unknown",
            "moneyness": (item.get("moneyness") or {}).get("moneyType", ""),
            "source": "QuantData order-flow/consolidated (latest page only)",
        })
        rows.append(row)
    return rows


def normalize_dark_pool(
    raw: dict, levels_raw: dict, run_at: datetime, symbol: str
) -> list[dict[str, object]]:
    cutoff_ms = int(run_at.timestamp() * 1000)
    levels = levels_raw.get("data", {})
    rows: list[dict[str, object]] = []
    for raw_ms, item in sorted(raw.get("data", {}).items(), key=lambda value: int(value[0])):
        stamp = datetime.fromtimestamp(int(raw_ms) / 1000, tz=timezone.utc).astimezone(ET)
        if stamp.date() != run_at.date() or int(raw_ms) > cutoff_ms:
            continue
        price = item.get("stockPrice", "")
        level = levels.get(str(price), {}) if price != "" else {}
        row = blank_row("dark_pool.csv")
        row.update({
            "timestamp": stamp.isoformat(),
            "symbol": symbol,
            "price": price,
            "size": item.get("size", ""),
            "notional": item.get("notionalValue", ""),
            "price_level_cumulative_volume": level.get("size", ""),
            "window": "1m",
            "source": "QuantData dark-flow; session price levels from dark-pool-levels",
        })
        rows.append(row)
    return rows


def last_price(raw: dict, run_at: datetime) -> float | None:
    cutoff = int(run_at.timestamp() * 1000)
    eligible = [(int(key), value) for key, value in raw.get("data", {}).items() if int(key) <= cutoff]
    if not eligible:
        return None
    return float(max(eligible, key=lambda item: item[0])[1]["closePrice"])


def source_definition(name: str, retrieved_at: str, data_timestamp: str, delay: float, **extra: object) -> dict:
    value = {
        "name": name,
        "retrieved_at": retrieved_at,
        "data_timestamp": data_timestamp,
        "feed_type": extra.pop("feed_type", "realtime"),
        "delay_seconds": delay,
        "perspective": extra.pop("perspective", "unknown"),
        "contract_multiplier": 100,
        "sign_convention": extra.pop("sign_convention", {"status": "unknown"}),
        "units": extra.pop("units", {"status": "unknown"}),
    }
    value.update(extra)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--snapshot-time", required=True)
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--asset-type", choices=("index", "equity", "etf"), default="index")
    parser.add_argument("--previous-close", type=float, required=True)
    parser.add_argument("--menthorq-json", type=Path, required=True)
    parser.add_argument("--volsignals-gamma-charm", type=Path)
    parser.add_argument("--volsignals-vanna-delta", type=Path)
    parser.add_argument("--menthorq-exposure", type=Path, required=True)
    parser.add_argument("--menthorq-matrix", type=Path, required=True)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    run_at = datetime.fromisoformat(args.timestamp)
    symbol = args.symbol.upper()
    symbol_lower = symbol.lower()
    session_date = run_at.date().isoformat()
    raw_dir = run_dir / "raw" / "quantdata"
    price_raw = read_json(raw_dir / f"quantdata_stock-price-over-time_{symbol_lower}_{session_date}.json")
    term_raw = read_json(raw_dir / f"quantdata_term-structure_{symbol_lower}.json")
    flow_raw = read_json(raw_dir / f"quantdata_order-flow-consolidated_{symbol_lower}.json")
    vix_raw = read_json(raw_dir / f"quantdata_stock-price-over-time_vix_{session_date}.json")
    dark_flow_path = raw_dir / f"quantdata_dark-flow_{symbol_lower}.json"
    dark_levels_path = raw_dir / f"quantdata_dark-pool-levels_{symbol_lower}.json"
    dark_flow_raw = read_json(dark_flow_path) if dark_flow_path.is_file() else {"data": {}}
    dark_levels_raw = read_json(dark_levels_path) if dark_levels_path.is_file() else {"data": {}}
    menthorq_raw = read_json(args.menthorq_json)

    underlying_rows, spot, realized_vol = normalize_underlying(price_raw, run_at, args.previous_close, symbol)
    iv_rows, front_iv = normalize_iv_surface(term_raw, run_at, realized_vol)
    flow_rows = normalize_flow(flow_raw, run_at)
    dark_pool_rows = normalize_dark_pool(dark_flow_raw, dark_levels_raw, run_at, symbol)
    write_rows(run_dir / "underlying_1m.csv", underlying_rows)
    write_rows(run_dir / "iv_surface.csv", iv_rows)
    write_rows(run_dir / "option_flow.csv", flow_rows)
    write_rows(run_dir / "dark_pool.csv", dark_pool_rows)

    regime = blank_row("market_regime.csv")
    regime.update({
        "timestamp": run_at.isoformat(),
        "VIX": last_price(vix_raw, run_at) or "",
        "SPX_atm_iv": front_iv if symbol == "SPX" and front_iv is not None else "",
        "SPX_realized_vol": realized_vol if symbol == "SPX" and realized_vol is not None else "",
        "source": "QuantData VIX stock-price-over-time" + (" + SPX term-structure" if symbol == "SPX" else ""),
    })
    write_rows(run_dir / "market_regime.csv", [regime])

    intraday_levels = menthorq_raw.get("gamma_levels_intraday") or {}
    intraday_matrix = menthorq_raw.get("options_matrix_intraday") or {}
    levels = {
        "timestamp": run_at.isoformat(),
        "symbol": symbol,
        "underlying_price": spot,
        "gamma_flip": None,
        "zero_gamma": None,
        "call_wall": intraday_levels.get("call_resistance"),
        "put_wall": intraday_levels.get("put_support"),
        "hvl": intraday_levels.get("hvl"),
        "volatility_trigger": None,
        "blind_spots": [],
        "positive_gamma_zones": [],
        "negative_gamma_zones": [],
        "liquidity_vacuums": [],
        "expected_move_upper": intraday_levels.get("max_1d"),
        "expected_move_lower": intraday_levels.get("min_1d"),
        "metadata": {
            "source": "MenthorQ gateway API",
            "data_timestamp": vendor_timestamp(intraday_levels.get("timestamp")),
            "retrieved_at": menthorq_raw.get("retrieved_at"),
            "field_mapping": {
                "call_wall": "call_resistance",
                "put_wall": "put_support",
                "hvl": "hvl (not relabeled as gamma_flip)",
                "expected_move_upper": "max_1d",
                "expected_move_lower": "min_1d",
            },
            "call_resistance_0dte": intraday_levels.get("call_resistance_0dte"),
            "put_support_0dte": intraday_levels.get("put_support_0dte"),
            "matrix_totals": intraday_matrix.get("totals"),
        },
    }
    write_json(run_dir / "levels.json", levels)

    sanitized_menthorq = {
        "source": menthorq_raw.get("source"),
        "retrieved_at": menthorq_raw.get("retrieved_at"),
        "ticker": menthorq_raw.get("ticker"),
        "gamma_levels_intraday": menthorq_raw.get("gamma_levels_intraday"),
        "options_matrix_intraday": menthorq_raw.get("options_matrix_intraday"),
        "gamma_levels_eod": menthorq_raw.get("gamma_levels"),
        "options_matrix_eod": menthorq_raw.get("options_matrix"),
        "gamma_insights": menthorq_raw.get("gamma_insights"),
    }
    write_json(run_dir / "source_data" / "menthorq.json", sanitized_menthorq)

    screenshots = run_dir / "screenshots"
    screenshots.mkdir(exist_ok=True)
    assets = {
        args.menthorq_exposure: screenshots / "menthorq_exposure.png",
        args.menthorq_matrix: screenshots / "menthorq_matrix.png",
    }
    if args.volsignals_gamma_charm:
        assets[args.volsignals_gamma_charm] = screenshots / "volsignals_gamma_charm.png"
    if args.volsignals_vanna_delta:
        assets[args.volsignals_vanna_delta] = screenshots / "volsignals_vanna_delta.png"
    for source, destination in assets.items():
        shutil.copy2(source, destination)

    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    qd_definition = next(
        (item for item in manifest.get("source_definitions", []) if item.get("name") == "QuantData"),
        {},
    )
    mq_data_timestamp = vendor_timestamp(intraday_levels.get("timestamp")) or run_at.isoformat()
    definitions = [
        source_definition(
            "QuantData",
            qd_definition.get("retrieved_at", datetime.now(timezone.utc).isoformat()),
            run_at.isoformat(),
            0,
            feed_type="historical",
            perspective="unknown",
            sign_convention={
                "gex_positive": "raw signed callExposure/putExposure; dealer/customer perspective unverified",
                "vanna_positive": "raw signed callExposure/putExposure; dealer/customer perspective unverified",
                "delta_positive": "raw signed callExposure/putExposure; dealer/customer perspective unverified",
            },
            units={
                "exposure": "PER_ONE_DOLLAR_MOVE; exact dollar/contract scaling unverified",
                "iv": "volatility percentage points",
                "underlying": "index points" if args.asset_type == "index" else "USD per share",
                "flow_premium": "USD",
            },
            calculation={
                "snapshot_time": args.snapshot_time,
                "endpoints": [
                    "stock-price-over-time", "term-structure", "exposure-by-strike",
                    "order-flow/consolidated", "dark-flow", "dark-pool-levels",
                ],
                "flow_coverage": "latest API page, 100 rows requested; rows after run timestamp excluded",
            },
        ),
        source_definition(
            "MenthorQ",
            menthorq_raw.get("retrieved_at", datetime.now(timezone.utc).isoformat()),
            mq_data_timestamp,
            max(0, (run_at - datetime.fromisoformat(mq_data_timestamp)).total_seconds()),
            perspective="unknown",
            sign_convention={"status": "unknown; gateway fields preserved verbatim"},
            units={
                "levels": "index points" if args.asset_type == "index" else "USD per share",
                "net_gex": "vendor gateway units, exact scaling unverified",
                "net_dex": "vendor gateway units, exact scaling unverified",
            },
            calculation={
                "frequency": "intraday",
                "eod_comparison_timestamp": vendor_timestamp((menthorq_raw.get("gamma_levels") or {}).get("timestamp")),
                "artifacts": ["source_data/menthorq.json", "screenshots/menthorq_exposure.png", "screenshots/menthorq_matrix.png"],
            },
        ),
    ]
    if args.volsignals_gamma_charm and args.volsignals_vanna_delta:
        vs_retrieved = datetime.fromtimestamp(args.volsignals_gamma_charm.stat().st_mtime, tz=ET).isoformat()
        definitions.insert(1, source_definition(
            "VolSignals", vs_retrieved, run_at.isoformat(), 0,
            perspective="unknown",
            sign_convention={"status": "unknown; screenshot only"},
            units={"status": "unknown; screenshot only"},
            calculation={"artifacts": ["screenshots/volsignals_gamma_charm.png", "screenshots/volsignals_vanna_delta.png"]},
        ))
    manifest.update({
        "spot": spot,
        "previous_close": args.previous_close,
        "expirations": sorted(term_raw.get("data", {}).keys()),
        "capture_started_at": min(
            datetime.fromtimestamp(source.stat().st_mtime, tz=ET) for source in assets
        ).isoformat(),
        "capture_completed_at": datetime.now(ET).isoformat(),
        "data_delay_seconds": 1500,
        "sources": [item["name"] for item in definitions],
        "source_definitions": definitions,
        "missing_files": ["short_data.json", "positions.json"],
        "data_coverage": {
            "complete": False,
            "available": {
                "underlying_1m.csv": f"{len(underlying_rows)} {symbol} regular-session one-minute OHLC rows; no volume/bid/ask",
                "iv_surface.csv": f"{len(iv_rows)} fixed-strike IV/delta points",
                "dealer_exposure.csv": "QuantData GAMMA/VANNA/DELTA by strike and expiration",
                "option_flow.csv": f"{len(flow_rows)} latest consolidated QuantData flow rows at or before run time",
                "market_regime.csv": (
                    "VIX plus computed SPX front ATM IV and realized volatility"
                    if symbol == "SPX" else "VIX at the common snapshot cutoff"
                ),
                "levels.json": "MenthorQ intraday HVL/resistance/support/1-day range",
                "dark_pool.csv": f"{len(dark_pool_rows)} QuantData one-minute dark-flow rows plus raw price-level aggregates",
            },
            "missing_datasets": [
                "complete option chain with NBBO/OI/full Greeks",
                "cliff/CDF/PDF levels derived from a complete same-time chain",
                "full-session option flow and 1-minute flow aggregation",
                "ES/SPY/NQ/QQQ and full market-regime cross-asset fields",
                "VolSignals numeric export/sign/unit definitions",
                f"VolSignals ticker coverage ({symbol} unavailable; subscription UI exposes SPX/VIX only)",
                "OptionDepth 3D numeric export",
                "short interest / borrow data",
            ],
        },
        "notes": f"Partial authorized intraday capture for {symbol} at {run_at.strftime('%H:%M')} ET. Non-strict only; header-only option_chain.csv and cliff_levels.csv are intentional and must not be treated as complete data.",
    })
    write_json(manifest_path, manifest)

    if symbol == "SPX":
        events = read_json(run_dir / "events.json")
        events["opex"] = [{
            "timestamp": f"{session_date}T16:00:00-04:00",
            "product": "SPXW", "settlement": "PM", "is_0dte": True,
            "status": "scheduled", "source": "QuantData expiration list",
        }]
        write_json(run_dir / "events.json", events)

    write_json(run_dir / "state.json", {
        "previous_regime": "partial_intraday",
        "previous_short_state": None,
        "previous_pivot": intraday_levels.get("hvl"),
        "previous_gamma_flip": None,
        "previous_call_wall": intraday_levels.get("call_resistance"),
        "previous_put_wall": intraday_levels.get("put_support"),
        "previous_iv_state": {"front_atm_iv": front_iv, "realized_vol": realized_vol},
        "previous_flow_state": {"coverage": "latest_100_consolidated_rows", "rows": len(flow_rows)},
        "previous_invalidation": None,
    })

    print(json.dumps({
        "run": str(run_dir),
        "spot": spot,
        "underlying_rows": len(underlying_rows),
        "iv_rows": len(iv_rows),
        "flow_rows": len(flow_rows),
        "dark_pool_rows": len(dark_pool_rows),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
