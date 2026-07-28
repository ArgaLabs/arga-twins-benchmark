from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from arga_twins_benchmark.conformance.models import CONFORMANCE_REGISTRY_PROTOCOL, ConformanceRegistry
from arga_twins_benchmark.conformance.registry import (
    catalog_verifier_bundles,
    registry_entry_template,
)

REPRESENTATIVE_INSTANCE = "stripe_price_normalization_v1_stripe_clean_001"
REPRESENTATIVE_FIXTURE_CASE_IDS = {
    "stripe_price_normalization_v1.clean.gold",
    "stripe_price_normalization_v1.clean.gold.semantic_equivalent",
    "stripe_price_normalization_v1.clean.create_replacement",
    "stripe_price_normalization_v1.clean.wrong_price",
    "stripe_price_normalization_v1.clean.collateral_mutation",
}
REPRESENTATIVE_FAILURE_ASSERTIONS = {
    "stripe_price_normalization_v1.clean.create_replacement": [
        "state_catalog_preserved",
        "mutation_target_price",
        "mutation_policy.default_deny",
    ],
    "stripe_price_normalization_v1.clean.wrong_price": [
        "state_target_price",
        "mutation_target_price",
        "mutation_policy.default_deny",
    ],
    "stripe_price_normalization_v1.clean.collateral_mutation": [
        "state_catalog_preserved",
        "mutation_policy.default_deny",
    ],
}
REPRESENTATIVE_COLLATERAL_EXPECTATIONS = {
    case_id: True
    for case_id in (
        "stripe_price_normalization_v1.clean.create_replacement",
        "stripe_price_normalization_v1.clean.wrong_price",
        "stripe_price_normalization_v1.clean.collateral_mutation",
    )
}


def render_registry(catalog_root: Path) -> str:
    bundles = catalog_verifier_bundles(catalog_root)
    entries: list[dict[str, object]] = []
    for instance_id, bundle in sorted(bundles.items()):
        representative = instance_id == REPRESENTATIVE_INSTANCE
        entries.append(
            registry_entry_template(
                instance_id=instance_id,
                verification=bundle.verification,
                instance_bundle_sha256=bundle.instance_bundle_sha256,
                evaluator_fixture_case_ids=(
                    REPRESENTATIVE_FIXTURE_CASE_IDS if representative else set()
                ),
                intended_failure_assertions=(
                    REPRESENTATIVE_FAILURE_ASSERTIONS if representative else {}
                ),
                negative_collateral_expectations=(
                    REPRESENTATIVE_COLLATERAL_EXPECTATIONS if representative else {}
                ),
            )
        )
    payload: dict[str, object] = {
        "kind": "verifier_conformance_registry",
        "protocol": CONFORMANCE_REGISTRY_PROTOCOL,
        "schema_version": "0.1",
        "entries": entries,
    }
    validated = ConformanceRegistry.model_validate(payload)
    return json.dumps(validated.model_dump(mode="json"), indent=2, sort_keys=False) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Regenerate or verify the explicit verifier-conformance registry.")
    parser.add_argument("--catalog-root", type=Path, default=Path("benchmark"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark/conformance/registry.json"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero instead of writing when the checked-in registry is stale.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    rendered = render_registry(args.catalog_root)
    if args.check:
        existing = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
        if existing != rendered:
            print(f"{args.output} is stale; regenerate it", file=sys.stderr)
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
