#!/usr/bin/env python3
"""Fail CI when the production Jev model fixture violates release safety gates.

Every selected fixture is scored and reported, but only the gate model
(default: the pinned hosted production model) can fail the run. Local and
research fixtures are advisory: they document the gap without blocking release.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

bench = importlib.import_module("tests.test_jev_decision_bench")
CORPUS_PATH = bench.CORPUS_PATH
FIXTURE_DIR = bench.FIXTURE_DIR
gate_failures = bench.gate_failures
load_fixtures = bench.load_fixtures
score_case = bench.score_case
summarize = bench.summarize


PRODUCTION_MODEL = "jev-1-13-0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument(
        "--gate-model",
        default=PRODUCTION_MODEL,
        help="recorded fixture whose gate failures fail the run; "
        "all other selected models are scored as advisory only",
    )
    args = parser.parse_args(argv)
    cases = {case["id"]: case for case in yaml.safe_load(CORPUS_PATH.read_text())}
    fixtures = load_fixtures()
    selected = args.models or sorted(fixtures)
    if args.gate_model not in fixtures:
        print(f"{args.gate_model}: production fixture not found", file=sys.stderr)
        return 2
    if args.gate_model not in selected:
        selected = [*selected, args.gate_model]
    if not selected:
        print(f"no recorded fixtures found in {FIXTURE_DIR}", file=sys.stderr)
        return 2
    failed = False
    for model in selected:
        fixture = fixtures.get(model)
        if fixture is None:
            print(f"{model}: fixture not found", file=sys.stderr)
            failed = True
            continue
        records = fixture["results"]
        outcomes = [
            score_case(cases[case_id], record["payload"])
            for case_id, record in records.items()
        ]
        latencies = [
            float(record["latency_ms"])
            for outcome, record in zip(outcomes, records.values())
            if outcome["source"] == "l1"
            and isinstance(record.get("latency_ms"), (int, float))
        ]
        summary = summarize(outcomes, latencies)
        errors = gate_failures(summary, outcomes)
        gated = model == args.gate_model
        print(
            json.dumps(
                {
                    "model": model,
                    "summary": summary,
                    "gate_failures": errors,
                    "gated": gated,
                },
                sort_keys=True,
            )
        )
        failed |= gated and bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
