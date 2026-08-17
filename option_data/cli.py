"""Command-line interface for creating and validating normalized data runs."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .contract import CONTRACT_VERSION, CSV_COLUMNS, validate_run, write_csv_template


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def init_run(args: argparse.Namespace) -> int:
    stamp = args.timestamp
    local = stamp.astimezone(ZoneInfo(args.timezone))
    symbol = args.symbol.upper()
    run_dir = args.output / local.strftime("%Y-%m-%d") / symbol / local.strftime("%H%M%S")
    if run_dir.exists() and any(run_dir.iterdir()) and not args.force:
        print(f"Refusing to overwrite non-empty run: {run_dir}", file=sys.stderr)
        return 2
    run_dir.mkdir(parents=True, exist_ok=True)

    for name, columns in CSV_COLUMNS.items():
        if name not in {"option_flow_1m.csv", "optiondepth_3d.csv"} or args.include_optional:
            write_csv_template(run_dir / name, columns)

    manifest = {
        "contract_version": CONTRACT_VERSION,
        "run_id": f"{symbol}_{local.strftime('%Y%m%d_%H%M%S')}",
        "symbol": symbol,
        "asset_type": args.asset_type,
        "timestamp": stamp.isoformat(),
        "capture_started_at": stamp.isoformat(),
        "capture_completed_at": stamp.isoformat(),
        "timezone": args.timezone,
        "session": args.session,
        "spot": args.spot,
        "previous_close": args.previous_close,
        "expirations": args.expiration,
        "data_delay_seconds": args.data_delay_seconds,
        "products": [
            {
                "symbol": symbol,
                "asset_type": args.asset_type,
                "contract_multiplier": args.contract_multiplier,
            }
        ],
        "sources": [],
        "source_definitions": [],
        "missing_files": [
            "option_flow_1m.csv", "optiondepth_3d.csv", "short_data.json", "positions.json"
        ],
        "notes": "Header-only run initialized; populate source data before strict validation.",
    }
    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "levels.json", {
        "timestamp": stamp.isoformat(), "symbol": symbol, "underlying_price": args.spot,
        "gamma_flip": None, "zero_gamma": None, "call_wall": None, "put_wall": None,
        "hvl": None, "volatility_trigger": None, "blind_spots": [],
        "positive_gamma_zones": [], "negative_gamma_zones": [], "liquidity_vacuums": [],
        "expected_move_upper": None, "expected_move_lower": None, "metadata": {},
    })
    _write_json(run_dir / "events.json", {
        "timestamp": stamp.isoformat(), "symbol": symbol, "economic_events": [],
        "earnings": [], "ex_dividend": [], "opex": [], "monthly_opex": [],
        "quarterly_opex": [], "index_rebalance": [], "fomc": [], "cpi": [], "nfp": [],
        "treasury_auction": [], "market_holiday": [], "early_close": [],
    })
    (run_dir / "screenshots").mkdir(exist_ok=True)
    print(run_dir)
    return 0


def validate(args: argparse.Namespace) -> int:
    result = validate_run(args.run_dir, strict=args.strict)
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")
    row_summary = ", ".join(f"{name}={count}" for name, count in sorted(result.rows.items()))
    print(f"rows: {row_summary or 'none'}")
    print("VALID" if result.ok else "INVALID")
    return 0 if result.ok else 1


def publish_latest(args: argparse.Namespace) -> int:
    result = validate_run(args.run_dir, strict=args.strict)
    if not result.ok:
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    latest = args.latest
    latest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.run_dir / "manifest.json", latest / "manifest.json")
    state_path = args.run_dir / "state.json"
    if state_path.is_file():
        shutil.copy2(state_path, latest / "state.json")
    elif not (latest / "state.json").exists():
        _write_json(latest / "state.json", {
            "previous_regime": None, "previous_short_state": None, "previous_pivot": None,
            "previous_gamma_flip": None, "previous_call_wall": None, "previous_put_wall": None,
            "previous_iv_state": None, "previous_flow_state": None,
            "previous_invalidation": None,
        })
    print(latest)
    return 0


def collect_quantdata_exposure(args: argparse.Namespace) -> int:
    from .quantdata import collect

    sign_convention = {
        "gex_positive": args.gex_positive,
        "vanna_positive": args.vanna_positive,
        "delta_positive": args.delta_positive,
    }
    units = {
        "gex": args.gex_unit,
        "vanna": args.vanna_unit,
        "delta": args.delta_unit,
    }
    try:
        output = collect(
            args.run_dir, args.ticker.upper(), args.timestamp.isoformat(), args.session_date,
            args.snapshot_time, args.expiration, args.representation, args.perspective,
            args.gex_unit, args.contract_multiplier, args.feed_type, args.delay_seconds,
            sign_convention, units, args.force,
        )
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="option-data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create one header-only run directory")
    init.add_argument("--symbol", required=True)
    init.add_argument("--asset-type", choices=("index", "future", "etf", "equity"), required=True)
    init.add_argument("--timestamp", type=_parse_iso, required=True)
    init.add_argument("--timezone", default="America/New_York")
    init.add_argument("--session", choices=("premarket", "overnight", "regular", "postmarket", "closed"), default="regular")
    init.add_argument("--spot", type=float, required=True)
    init.add_argument("--previous-close", type=float, required=True)
    init.add_argument("--expiration", action="append", default=[])
    init.add_argument("--data-delay-seconds", type=int, default=0)
    init.add_argument("--contract-multiplier", type=float, default=100)
    init.add_argument("--output", type=Path, default=Path("runs"))
    init.add_argument("--include-optional", action="store_true")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=init_run)

    check = subparsers.add_parser("validate", help="Validate one run directory")
    check.add_argument("run_dir", type=Path)
    check.add_argument("--strict", action="store_true")
    check.set_defaults(func=validate)

    latest = subparsers.add_parser("publish-latest", help="Validate and update latest pointers")
    latest.add_argument("run_dir", type=Path)
    latest.add_argument("--latest", type=Path, default=Path("latest"))
    latest.add_argument("--strict", action="store_true")
    latest.set_defaults(func=publish_latest)

    qd = subparsers.add_parser(
        "collect-quantdata-exposure",
        help="Fetch direct QuantData GAMMA/VANNA/DELTA by strike and normalize it",
    )
    qd.add_argument("run_dir", type=Path)
    qd.add_argument("--ticker", required=True)
    qd.add_argument("--timestamp", type=_parse_iso, required=True, help="Data as-of time")
    qd.add_argument("--session-date")
    qd.add_argument("--snapshot-time", help="QuantData UTC snapshotTime")
    qd.add_argument("--expiration")
    qd.add_argument("--representation", choices=("PER_ONE_DOLLAR_MOVE", "RAW"), required=True)
    qd.add_argument("--perspective", choices=("dealer", "customer", "market", "unknown"), required=True)
    qd.add_argument("--feed-type", choices=("realtime", "delayed", "end_of_day", "historical", "unknown"), required=True)
    qd.add_argument("--delay-seconds", type=float, required=True)
    qd.add_argument("--contract-multiplier", type=float, required=True)
    qd.add_argument("--gex-positive", required=True)
    qd.add_argument("--vanna-positive", required=True)
    qd.add_argument("--delta-positive", required=True)
    qd.add_argument("--gex-unit", required=True)
    qd.add_argument("--vanna-unit", required=True)
    qd.add_argument("--delta-unit", required=True)
    qd.add_argument("--force", action="store_true")
    qd.set_defaults(func=collect_quantdata_exposure)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
