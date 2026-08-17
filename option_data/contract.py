"""Canonical file names, CSV headers, and lightweight semantic validation."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CONTRACT_VERSION = "1.0.0"

CSV_COLUMNS: dict[str, tuple[str, ...]] = {
    "underlying_1m.csv": (
        "timestamp", "symbol", "session", "open", "high", "low", "close",
        "bid", "ask", "bid_size", "ask_size", "volume", "vwap", "trade_count",
        "previous_close", "day_open", "overnight_high", "overnight_low", "day_high",
        "day_low", "is_new_high", "is_new_low", "vwap_position", "higher_high",
        "lower_low", "atr", "realized_vol", "price_velocity", "price_acceleration",
        "distance_call_wall", "distance_put_wall", "distance_gamma_flip", "source",
    ),
    "option_chain.csv": (
        "timestamp", "underlying", "option_symbol", "expiration", "dte", "strike",
        "call_put", "bid", "ask", "mid", "bid_size", "ask_size", "last", "volume",
        "open_interest", "iv", "delta", "gamma", "vega", "theta", "vanna", "charm",
        "vomma", "underlying_price", "quote_timestamp", "contract_multiplier",
        "interest_rate", "dividend_yield", "oi_today", "oi_next_day", "oi_change",
        "volume_during_session", "iv_change", "delta_change", "gamma_change", "mid_change",
        "source",
    ),
    "iv_surface.csv": (
        "timestamp", "expiration", "dte", "strike", "call_put", "delta", "iv",
        "underlying_price", "coordinate_type", "atm_iv", "10_delta_put_iv",
        "25_delta_put_iv", "25_delta_call_iv", "10_delta_call_iv", "rr25", "bf25",
        "call_skew", "put_skew", "wing_skew", "front_iv", "back_iv", "term_slope",
        "realized_vol", "iv_minus_rv", "stickiness", "source",
    ),
    "dealer_exposure.csv": (
        "timestamp", "expiration", "strike", "call_put", "gex", "vanna_or_vex",
        "charm", "delta_exposure", "gamma_gradient", "vanna_gradient", "charm_gradient",
        "delta_change", "net_gex", "call_gex", "put_gex", "net_vanna", "net_charm",
        "net_delta_exposure", "zero_gamma", "gamma_flip", "underlying_price",
        "contract_multiplier", "perspective", "gex_unit", "source",
    ),
    "option_flow.csv": (
        "timestamp", "option_symbol", "underlying", "expiration", "dte", "strike",
        "call_put", "trade_price", "bid", "ask", "trade_size", "premium", "volume",
        "open_interest", "underlying_price", "iv", "delta", "exchange", "trade_condition",
        "sweep", "block", "multi_leg", "aggressor_side", "opening_closing_inference",
        "moneyness", "source",
    ),
    "option_flow_1m.csv": (
        "timestamp", "underlying", "ask_call_premium_1m", "bid_call_premium_1m",
        "ask_put_premium_1m", "bid_put_premium_1m", "call_sweep_count", "put_sweep_count",
        "net_call_aggression", "net_put_aggression", "same_contract_bid_after_ask", "source",
    ),
    "cliff_levels.csv": (
        "timestamp", "expiration", "strike", "call_mid", "call_mid_next", "put_mid",
        "put_mid_next", "delta_k", "prob_above", "prob_below", "density", "call_cliff",
        "put_cliff", "floor", "upper_boundary", "tail_top", "peak_density",
        "spot_cliff_gap", "vitality", "cross_expiry_alignment", "comparison_anchor", "source",
    ),
    "optiondepth_3d.csv": (
        "timestamp", "spot_grid", "expiration_or_time_grid", "strike", "gex", "vanna",
        "charm", "delta_exposure", "underlying_price", "selected_expiration", "source",
    ),
    "market_regime.csv": (
        "timestamp", "VIX", "VIX1D", "VIX9D", "VVIX", "front_vix_future",
        "second_vix_future", "vix_term_slope", "SPX_atm_iv", "SPX_realized_vol",
        "ES", "NQ", "RTY", "DXY", "US2Y", "US10Y", "ADD", "TICK",
        "up_volume", "down_volume", "put_call_ratio", "correlation_index",
        "cta_estimate", "vol_control_estimate", "ES_relative_strength",
        "NQ_relative_strength", "RTY_relative_strength", "source",
    ),
    "dark_pool.csv": (
        "timestamp", "symbol", "price", "size", "notional", "exchange_or_ats",
        "trade_condition", "relative_volume", "price_level_cumulative_volume", "dp_pct",
        "window", "accumulation_distribution_slope", "cost_zone_position",
        "large_distribution", "floor_support", "source",
    ),
}

JSON_FILES = (
    "manifest.json", "levels.json", "events.json", "short_data.json", "positions.json",
)

REQUIRED_FILES = (
    "manifest.json", "underlying_1m.csv", "option_chain.csv", "iv_surface.csv",
    "dealer_exposure.csv", "option_flow.csv", "cliff_levels.csv", "levels.json",
    "market_regime.csv", "events.json",
)

OPTIONAL_FILES = (
    "option_flow_1m.csv", "optiondepth_3d.csv", "dark_pool.csv", "short_data.json",
    "positions.json",
)

ENUMS = {
    "call_put": {"call", "put"},
    "session": {"premarket", "overnight", "regular", "postmarket", "closed"},
    "aggressor_side": {"ask", "bid", "mid", "unknown"},
    "perspective": {"dealer", "customer", "market", "unknown"},
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp has no UTC offset")
    return parsed


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _read_json(path: Path, result: ValidationResult) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.errors.append(f"{path.name}: invalid JSON: {exc}")
        return None


def _validate_manifest(path: Path, result: ValidationResult) -> dict[str, Any] | None:
    value = _read_json(path, result)
    if not isinstance(value, dict):
        if value is not None:
            result.errors.append("manifest.json: root must be an object")
        return None

    required = {
        "contract_version", "run_id", "symbol", "asset_type", "timestamp", "timezone",
        "session", "spot", "previous_close", "expirations", "data_delay_seconds", "sources",
        "source_definitions", "missing_files",
    }
    for key in sorted(required - value.keys()):
        result.errors.append(f"manifest.json: missing required key {key!r}")

    if value.get("contract_version") != CONTRACT_VERSION:
        result.errors.append(
            f"manifest.json: contract_version must be {CONTRACT_VERSION!r}"
        )
    try:
        stamp = parse_timestamp(str(value.get("timestamp", "")))
        zone = ZoneInfo(str(value.get("timezone", "")))
        if stamp.astimezone(zone).utcoffset() != stamp.utcoffset():
            result.warnings.append(
                "manifest.json: timestamp offset differs from named timezone at that instant"
            )
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        result.errors.append(f"manifest.json: invalid timestamp/timezone: {exc}")

    if not _is_number(str(value.get("spot", ""))):
        result.errors.append("manifest.json: spot must be numeric")
    if not _is_number(str(value.get("previous_close", ""))):
        result.errors.append("manifest.json: previous_close must be numeric")
    if not isinstance(value.get("expirations"), list):
        result.errors.append("manifest.json: expirations must be an array")

    sources = value.get("sources")
    definitions = value.get("source_definitions")
    if not isinstance(sources, list) or not all(isinstance(v, str) for v in sources):
        result.errors.append("manifest.json: sources must be an array of platform names")
    if not isinstance(definitions, list):
        result.errors.append("manifest.json: source_definitions must be an array")
    else:
        required_source_keys = {
            "name", "retrieved_at", "data_timestamp", "feed_type", "delay_seconds",
            "perspective", "contract_multiplier", "sign_convention", "units",
        }
        names: set[str] = set()
        for index, source in enumerate(definitions):
            if not isinstance(source, dict):
                result.errors.append(
                    f"manifest.json: source_definitions[{index}] must be an object"
                )
                continue
            names.add(str(source.get("name", "")))
            for key in sorted(required_source_keys - source.keys()):
                result.errors.append(
                    f"manifest.json: source_definitions[{index}] missing {key!r}"
                )
            for time_key in ("retrieved_at", "data_timestamp"):
                try:
                    parse_timestamp(str(source.get(time_key, "")))
                except ValueError as exc:
                    result.errors.append(
                        f"manifest.json: source_definitions[{index}].{time_key}: {exc}"
                    )
        if isinstance(sources, list) and set(sources) != names:
            result.errors.append(
                "manifest.json: sources and source_definitions names must match exactly"
            )
    return value


def _validate_csv(path: Path, expected: tuple[str, ...], result: ValidationResult) -> None:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            actual = tuple(reader.fieldnames or ())
            missing = [column for column in expected if column not in actual]
            if missing:
                result.errors.append(f"{path.name}: missing columns: {', '.join(missing)}")
            unknown = [column for column in actual if column not in expected]
            if unknown:
                result.warnings.append(f"{path.name}: extension columns: {', '.join(unknown)}")

            count = 0
            for line_number, row in enumerate(reader, start=2):
                count += 1
                timestamp = row.get("timestamp", "")
                if timestamp:
                    try:
                        parse_timestamp(timestamp)
                    except ValueError as exc:
                        result.errors.append(f"{path.name}:{line_number}: timestamp: {exc}")
                for enum_field, allowed in ENUMS.items():
                    raw = row.get(enum_field, "")
                    if raw and raw.lower() not in allowed:
                        result.errors.append(
                            f"{path.name}:{line_number}: invalid {enum_field}={raw!r}"
                        )
                bid, ask = row.get("bid", ""), row.get("ask", "")
                if _is_number(bid) and _is_number(ask) and float(bid) > float(ask):
                    result.errors.append(f"{path.name}:{line_number}: bid exceeds ask")
                dte = row.get("dte", "")
                if _is_number(dte) and float(dte) < 0:
                    result.errors.append(f"{path.name}:{line_number}: dte is negative")
            result.rows[path.name] = count
    except OSError as exc:
        result.errors.append(f"{path.name}: could not read: {exc}")


def validate_run(run_dir: Path, strict: bool = False) -> ValidationResult:
    """Validate one normalized run directory.

    Non-strict validation accepts header-only files so templates and partial captures can
    be checked. Strict mode requires every mandatory dataset to contain at least one row
    and rejects manifest-declared missing mandatory files.
    """

    run_dir = run_dir.resolve()
    result = ValidationResult()
    if not run_dir.is_dir():
        result.errors.append(f"run directory does not exist: {run_dir}")
        return result

    missing_on_disk = [name for name in REQUIRED_FILES if not (run_dir / name).is_file()]
    for name in missing_on_disk:
        result.errors.append(f"missing required file: {name}")

    manifest = None
    if (run_dir / "manifest.json").is_file():
        manifest = _validate_manifest(run_dir / "manifest.json", result)

    for name, columns in CSV_COLUMNS.items():
        path = run_dir / name
        if path.is_file():
            _validate_csv(path, columns, result)

    for name in ("levels.json", "events.json", "short_data.json", "positions.json"):
        path = run_dir / name
        if path.is_file():
            _read_json(path, result)

    if manifest is not None:
        declared = manifest.get("missing_files", [])
        if not isinstance(declared, list) or not all(isinstance(v, str) for v in declared):
            result.errors.append("manifest.json: missing_files must be an array of file names")
        else:
            actual_optional_missing = {
                name for name in OPTIONAL_FILES if not (run_dir / name).is_file()
            }
            undeclared = actual_optional_missing - set(declared)
            if undeclared:
                result.warnings.append(
                    "manifest.json: optional missing files not declared: "
                    + ", ".join(sorted(undeclared))
                )
            declared_but_present = {
                name for name in declared if (run_dir / name).exists()
            }
            if declared_but_present:
                result.errors.append(
                    "manifest.json: files declared missing but present: "
                    + ", ".join(sorted(declared_but_present))
                )
            if strict and set(declared) & set(REQUIRED_FILES):
                result.errors.append("manifest.json: strict run declares mandatory data missing")

    if strict:
        if manifest is not None:
            if not manifest.get("sources"):
                result.errors.append("manifest.json: strict run requires at least one source")
            for index, source in enumerate(manifest.get("source_definitions", [])):
                if not isinstance(source, dict):
                    continue
                if source.get("feed_type") == "unknown":
                    result.errors.append(
                        f"manifest.json: strict run has unknown feed_type for source_definitions[{index}]"
                    )
                if source.get("perspective") == "unknown":
                    result.errors.append(
                        f"manifest.json: strict run has unknown perspective for source_definitions[{index}]"
                    )
                serialized_convention = json.dumps(source.get("sign_convention", {})).lower()
                serialized_units = json.dumps(source.get("units", {})).lower()
                if "unknown" in serialized_convention:
                    result.errors.append(
                        f"manifest.json: strict run has unknown sign convention for source_definitions[{index}]"
                    )
                if "unknown" in serialized_units:
                    result.errors.append(
                        f"manifest.json: strict run has unknown units for source_definitions[{index}]"
                    )
        levels_path = run_dir / "levels.json"
        if levels_path.is_file():
            levels = _read_json(levels_path, result)
            if isinstance(levels, dict):
                for key in ("gamma_flip", "zero_gamma", "call_wall", "put_wall"):
                    if levels.get(key) is None:
                        result.errors.append(
                            f"levels.json: strict run requires a non-null {key}"
                        )
        for name in REQUIRED_FILES:
            if name.endswith(".csv") and result.rows.get(name, 0) == 0:
                result.errors.append(f"{name}: strict mode requires at least one data row")

    return result


def write_csv_template(path: Path, columns: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(columns)
