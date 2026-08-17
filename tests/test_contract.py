from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from option_data.cli import main
from option_data.contract import CSV_COLUMNS, validate_run
from option_data.quantdata import normalize_exposures


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "runs" / "2026-08-14" / "SPX" / "155800"


class ContractTests(unittest.TestCase):
    def test_example_is_structurally_valid(self) -> None:
        result = validate_run(EXAMPLE)
        self.assertTrue(result.ok, result.errors)
        self.assertFalse(result.warnings, result.warnings)

    def test_example_is_not_publishable_market_data(self) -> None:
        result = validate_run(EXAMPLE, strict=True)
        self.assertFalse(result.ok)
        self.assertTrue(any("requires at least one data row" in item for item in result.errors))
        self.assertTrue(any("unknown perspective" in item for item in result.errors))
        self.assertTrue(any("non-null gamma_flip" in item for item in result.errors))

    def test_cli_init_creates_a_valid_header_only_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "runs"
            code = main([
                "init", "--symbol", "SPX", "--asset-type", "index",
                "--timestamp", "2026-08-14T15:58:00-04:00", "--spot", "7776.55",
                "--previous-close", "7799.19", "--expiration", "2026-08-14",
                "--output", str(output),
            ])
            self.assertEqual(code, 0)
            run_dir = output / "2026-08-14" / "SPX" / "155800"
            result = validate_run(run_dir)
            self.assertTrue(result.ok, result.errors)

    def test_bid_above_ask_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "run"
            run_dir.mkdir()
            for source in EXAMPLE.iterdir():
                if source.is_file():
                    (run_dir / source.name).write_bytes(source.read_bytes())
            chain = run_dir / "option_chain.csv"
            with chain.open("a", encoding="utf-8") as handle:
                row = {column: "" for column in CSV_COLUMNS["option_chain.csv"]}
                row.update({
                    "timestamp": "2026-08-14T15:58:00-04:00", "underlying": "SPX",
                    "option_symbol": "EXAMPLE", "expiration": "2026-08-14", "dte": "0",
                    "strike": "7775", "call_put": "call", "bid": "11", "ask": "10",
                })
                writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS["option_chain.csv"])
                writer.writerow(row)
            result = validate_run(run_dir)
            self.assertTrue(any("bid exceeds ask" in item for item in result.errors))

    def test_all_json_schemas_parse(self) -> None:
        for path in (ROOT / "schemas").glob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_example_headers_match_contract(self) -> None:
        for name in (
            "underlying_1m.csv", "option_chain.csv", "iv_surface.csv",
            "dealer_exposure.csv", "option_flow.csv", "cliff_levels.csv",
            "market_regime.csv",
        ):
            with (EXAMPLE / name).open(newline="", encoding="utf-8") as handle:
                fields = tuple(csv.reader(handle).__next__())
            self.assertEqual(fields, CSV_COLUMNS[name], name)


class QuantDataTests(unittest.TestCase):
    def test_normalize_merges_greeks_and_preserves_call_put(self) -> None:
        def payload(call: float, put: float) -> dict:
            return {
                "data": {
                    "SPX": {
                        "stockPrice": 5000,
                        "exposureMap": {
                            "2026-08-14": {
                                "5000": {"callExposure": call, "putExposure": put}
                            }
                        },
                    }
                }
            }

        rows = normalize_exposures(
            {"GAMMA": payload(10, -4), "VANNA": payload(3, -1), "DELTA": payload(8, -2)},
            "SPX", "2026-08-14T15:58:00-04:00", "dealer", "USD_per_1_point", 100,
        )
        self.assertEqual(len(rows), 2)
        call = next(row for row in rows if row["call_put"] == "call")
        put = next(row for row in rows if row["call_put"] == "put")
        self.assertEqual(call["gex"], 10)
        self.assertEqual(call["vanna_or_vex"], 3)
        self.assertEqual(put["delta_exposure"], -2)
        self.assertEqual(call["net_gex"], 6)
        self.assertEqual(put["net_vanna"], 2)


if __name__ == "__main__":
    unittest.main()
