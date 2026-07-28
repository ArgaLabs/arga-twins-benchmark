from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from arga_twins_benchmark.conformance import live
from arga_twins_benchmark.conformance.registry import (
    CatalogVerifierBundle,
    catalog_verifier_bundles,
)
from arga_twins_benchmark.evaluation.state_capture import (
    CapturedProviderState,
    TrustedStateSnapshot,
)


def test_live_reset_isolation_uses_cli_lifecycle_hooks_and_remains_partial_without_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    instance_id = "stripe_price_normalization_v1_stripe_clean_001"
    bundle = catalog_verifier_bundles(Path("benchmark"))[instance_id]
    lifecycle_calls = {"provision": 0, "reset": 0, "cleanup": 0}
    run_ids = iter(("run-first", "run-second"))

    async def fake_provision_instance(
        *,
        catalog_root: Path,
        instance_id: str,
        control_output: Path,
        candidate_output: Path,
        ttl_minutes: int,
        timeout_seconds: int,
        arga_candidate_safe_profile: bool,
    ) -> None:
        del catalog_root, ttl_minutes, timeout_seconds, arga_candidate_safe_profile
        lifecycle_calls["provision"] += 1
        run_id = next(run_ids)
        control_output.write_text(
            json.dumps(
                    {
                        "protocol": "arga-bench-control/1",
                        "instance_id": instance_id,
                        "scenario_id": "scenario-test",
                        "run_id": run_id,
                    "twin_run": {"run_id": run_id, "status": "ready", "twins": {}},
                }
            ),
            encoding="utf-8",
        )
        candidate_output.write_text("{}", encoding="utf-8")

    async def fake_reset_instance(control_file: Path) -> dict[str, Any]:
        assert control_file.is_file()
        lifecycle_calls["reset"] += 1
        return {"status": "ready"}

    async def fake_cleanup_instance(control_file: Path) -> dict[str, Any]:
        assert control_file.is_file()
        lifecycle_calls["cleanup"] += 1
        return {"confirmed": True}

    class FakeCapturer:
        async def capture(
            self,
            control_payload: dict[str, Any],
            *,
            roles: dict[str, str],
            snapshot_queries: object,
        ) -> TrustedStateSnapshot:
            del control_payload, roles, snapshot_queries
            return TrustedStateSnapshot(
                providers={
                    "stripe": CapturedProviderState(
                        provider_name="stripe",
                        provider_role="payments",
                        state={"stable": True},
                    )
                }
            )

    def fake_catalog_verifier_bundles(catalog_root: Path) -> dict[str, CatalogVerifierBundle]:
        del catalog_root
        return {instance_id: bundle}

    def fake_cleanup_proves_inert(
        payload: Mapping[str, Any] | None,
        *,
        expected_run_id: str | None = None,
    ) -> bool:
        del payload, expected_run_id
        return True

    monkeypatch.setattr(live, "catalog_verifier_bundles", fake_catalog_verifier_bundles)
    monkeypatch.setattr(live, "provision_instance", fake_provision_instance)
    monkeypatch.setattr(live, "reset_instance", fake_reset_instance)
    monkeypatch.setattr(live, "cleanup_instance", fake_cleanup_instance)
    monkeypatch.setattr(live, "cleanup_payload_proves_inert", fake_cleanup_proves_inert)
    monkeypatch.setattr(live, "TrustedStateCapturer", FakeCapturer)

    output = tmp_path / "lifecycle.json"
    evidence = asyncio.run(
        live.run_live_reset_isolation(
            catalog_root=Path("benchmark"),
            instance_id=instance_id,
            output=output,
            reset_count=10,
        )
    )

    assert lifecycle_calls == {"provision": 2, "reset": 10, "cleanup": 2}
    assert len(evidence.reset_state_sha256) == 10
    assert set(evidence.reset_state_sha256) == {evidence.baseline_state_sha256}
    assert evidence.independent_run_proved is True
    assert evidence.mutation_visibility_proved is False
    assert evidence.cleanup_confirmed is True
    assert output.stat().st_mode & 0o777 == 0o600
