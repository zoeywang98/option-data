"""Direct QuantData exposure-by-strike adapter.

This adapter deliberately requires convention metadata from the caller. It does not
guess whether a vendor value represents dealers or customers, or how the vendor scales
an exposure.
"""

from __future__ import annotations

import csv
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contract import CSV_COLUMNS


API_URL = "https://api.quantdata.us/v1/options/tool/exposure-by-strike"
GREEK_FIELDS = {
    "GAMMA": "gex",
    "VANNA": "vanna_or_vex",
    "DELTA": "delta_exposure",
}


def fetch_exposure(
    api_key: str,
    ticker: str,
    greek_mode: str,
    representation_mode: str,
    session_date: str | None = None,
    snapshot_time: str | None = None,
    expiration: str | None = None,
) -> dict[str, Any]:
    request_filter: dict[str, Any] = {"ticker": ticker.upper()}
    if expiration:
        request_filter["expirationDate"] = expiration
    body: dict[str, Any] = {
        "greekMode": greek_mode,
        "representationMode": representation_mode,
        "filter": request_filter,
    }
    if session_date:
        body["sessionDate"] = session_date
    if snapshot_time:
        body["snapshotTime"] = snapshot_time

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "option-data-contract/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"QuantData returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach QuantData: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("QuantData returned a non-object response")
    return payload


def _ticker_payload(payload: dict[str, Any], ticker: str) -> dict[str, Any]:
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise RuntimeError("QuantData response.data is not an object")
    value = data.get(ticker.upper())
    if value is None and data:
        value = next(iter(data.values()))
    if not isinstance(value, dict):
        raise RuntimeError(f"No exposure data returned for {ticker.upper()}")
    return value


def normalize_exposures(
    payloads: dict[str, dict[str, Any]],
    ticker: str,
    timestamp: str,
    perspective: str,
    gex_unit: str,
    contract_multiplier: float,
) -> list[dict[str, Any]]:
    """Merge GAMMA/VANNA/DELTA responses into the dealer_exposure contract."""

    merged: dict[tuple[str, float, str], dict[str, Any]] = {}
    stock_price: float | None = None
    totals = {
        "call_gex": 0.0,
        "put_gex": 0.0,
        "net_vanna": 0.0,
        "net_delta_exposure": 0.0,
    }

    for greek_mode, payload in payloads.items():
        if greek_mode not in GREEK_FIELDS:
            continue
        value = _ticker_payload(payload, ticker)
        if value.get("stockPrice") is not None:
            stock_price = float(value["stockPrice"])
        exposure_map = value.get("exposureMap", {})
        if not isinstance(exposure_map, dict):
            raise RuntimeError(f"{greek_mode} exposureMap is not an object")
        for expiration, strikes in exposure_map.items():
            if not isinstance(strikes, dict):
                continue
            for strike_raw, cell in strikes.items():
                if not isinstance(cell, dict):
                    continue
                strike = float(strike_raw)
                for call_put, vendor_key in (("call", "callExposure"), ("put", "putExposure")):
                    raw = cell.get(vendor_key)
                    exposure = float(raw or 0)
                    key = (str(expiration), strike, call_put)
                    row = merged.setdefault(key, {})
                    row[GREEK_FIELDS[greek_mode]] = exposure
                    if greek_mode == "GAMMA":
                        totals[f"{call_put}_gex"] += exposure
                    elif greek_mode == "VANNA":
                        totals["net_vanna"] += exposure
                    elif greek_mode == "DELTA":
                        totals["net_delta_exposure"] += exposure

    net_gex = totals["call_gex"] + totals["put_gex"]
    rows: list[dict[str, Any]] = []
    for (expiration, strike, call_put), exposures in sorted(merged.items()):
        row = {column: "" for column in CSV_COLUMNS["dealer_exposure.csv"]}
        row.update({
            "timestamp": timestamp,
            "expiration": expiration,
            "strike": strike,
            "call_put": call_put,
            "gex": exposures.get("gex", ""),
            "vanna_or_vex": exposures.get("vanna_or_vex", ""),
            "delta_exposure": exposures.get("delta_exposure", ""),
            "net_gex": net_gex,
            "call_gex": totals["call_gex"],
            "put_gex": totals["put_gex"],
            "net_vanna": totals["net_vanna"],
            "net_delta_exposure": totals["net_delta_exposure"],
            "underlying_price": stock_price if stock_price is not None else "",
            "contract_multiplier": contract_multiplier,
            "perspective": perspective,
            "gex_unit": gex_unit,
            "source": "QuantData",
        })
        rows.append(row)
    return rows


def collect(
    run_dir: Path,
    ticker: str,
    timestamp: str,
    session_date: str | None,
    snapshot_time: str | None,
    expiration: str | None,
    representation_mode: str,
    perspective: str,
    gex_unit: str,
    contract_multiplier: float,
    feed_type: str,
    delay_seconds: float,
    sign_convention: dict[str, str],
    units: dict[str, str],
    force: bool = False,
) -> Path:
    api_key = os.environ.get("QUANTDATA_API_KEY")
    if not api_key:
        raise RuntimeError("Set QUANTDATA_API_KEY; credentials are never written to the run")

    output = run_dir / "dealer_exposure.csv"
    if output.exists() and output.stat().st_size > 400 and not force:
        raise RuntimeError(f"Refusing to overwrite populated file: {output}")

    payloads = {
        greek: fetch_exposure(
            api_key, ticker, greek, representation_mode, session_date, snapshot_time, expiration
        )
        for greek in GREEK_FIELDS
    }
    raw_dir = run_dir / "raw" / "quantdata"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for greek, payload in payloads.items():
        (raw_dir / f"exposure-by-strike_{greek.lower()}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    rows = normalize_exposures(
        payloads, ticker, timestamp, perspective, gex_unit, contract_multiplier
    )
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS["dealer_exposure.csv"])
        writer.writeheader()
        writer.writerows(rows)

    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retrieved_at = datetime.now(timezone.utc).isoformat()
    source_definition = {
        "name": "QuantData",
        "retrieved_at": retrieved_at,
        "data_timestamp": timestamp,
        "feed_type": feed_type,
        "delay_seconds": delay_seconds,
        "perspective": perspective,
        "contract_multiplier": contract_multiplier,
        "sign_convention": sign_convention,
        "units": units,
        "calculation": {
            "greek_modes": list(GREEK_FIELDS),
            "representation_mode": representation_mode,
            "session_date": session_date,
            "snapshot_time": snapshot_time,
            "expiration_filter": expiration,
        },
        "raw_path": "raw/quantdata",
    }
    definitions = [
        item for item in manifest.get("source_definitions", []) if item.get("name") != "QuantData"
    ]
    definitions.append(source_definition)
    manifest["source_definitions"] = definitions
    manifest["sources"] = [item["name"] for item in definitions]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output
