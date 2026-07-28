from __future__ import annotations

import json
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from arga_twins_benchmark.conformance.models import LIVE_LIFECYCLE_PROTOCOL, LiveLifecycleEvidence
from arga_twins_benchmark.conformance.registry import (
    ConformanceError,
    catalog_verifier_bundles,
    stable_json_sha256,
)
from arga_twins_benchmark.evaluation.state_capture import TrustedStateCapturer
from arga_twins_benchmark.lifecycle import (
    cleanup_instance,
    cleanup_payload_proves_inert,
    provision_instance,
    read_control_ids,
    reset_instance,
    write_private_json,
)

type MutationVisibilityProbe = Callable[[Path], Awaitable[None]]


async def run_live_reset_isolation(
    *,
    catalog_root: Path,
    instance_id: str,
    output: Path,
    reset_count: int = 10,
    ttl_minutes: int = 60,
    timeout_seconds: int = 600,
    arga_candidate_safe_profile: bool = False,
    mutation_probe: MutationVisibilityProbe | None = None,
) -> LiveLifecycleEvidence:
    """Exercise reset/fresh-run isolation through the Arga CLI lifecycle only.

    Provisioning, every reset, and teardown call the public lifecycle helpers,
    which invoke ``arga`` as a subprocess. Trusted state reads use the
    verifier-only provider surfaces returned by that CLI control record. This
    function never calls an Arga server endpoint directly.

    Without ``mutation_probe`` the result deliberately remains a partial live
    record: deterministic resets and a fresh independent run can be proved, but
    mutation visibility cannot. The release audit therefore remains closed.
    """

    if reset_count < 10:
        raise ConformanceError("live conformance requires at least ten equivalent resets")
    bundles = catalog_verifier_bundles(catalog_root)
    bundle = bundles.get(instance_id)
    if bundle is None:
        raise ConformanceError(f"unknown benchmark instance {instance_id!r}")

    with tempfile.TemporaryDirectory(prefix="arga-bench-conformance-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        first_control = temporary_root / "first-control.json"
        first_candidate = temporary_root / "first-candidate.json"
        second_control = temporary_root / "second-control.json"
        second_candidate = temporary_root / "second-candidate.json"
        controls: list[Path] = []
        cleanup_confirmed = True
        capture = TrustedStateCapturer()
        baseline_sha256: str | None = None
        reset_hashes: list[str] = []
        independent_sha256: str | None = None
        independent_run_proved = False
        mutation_visibility_proved = False
        mutation_sha256: str | None = None
        post_probe_reset_sha256: str | None = None
        primary_error: BaseException | None = None
        cleanup_error: BaseException | None = None

        try:
            await provision_instance(
                catalog_root=catalog_root,
                instance_id=instance_id,
                control_output=first_control,
                candidate_output=first_candidate,
                ttl_minutes=ttl_minutes,
                timeout_seconds=timeout_seconds,
                arga_candidate_safe_profile=arga_candidate_safe_profile,
            )
            controls.append(first_control)
            first_payload = _read_json_object(first_control)
            baseline = await capture.capture(
                first_payload,
                roles=bundle.binding.roles,
                snapshot_queries=bundle.verification.deterministic.snapshot_queries,
            )
            baseline_sha256 = stable_json_sha256(baseline.artifact_payload())

            for _ in range(reset_count):
                await reset_instance(first_control)
                reset_snapshot = await capture.capture(
                    _read_json_object(first_control),
                    roles=bundle.binding.roles,
                    snapshot_queries=bundle.verification.deterministic.snapshot_queries,
                )
                reset_hashes.append(stable_json_sha256(reset_snapshot.artifact_payload()))

            await provision_instance(
                catalog_root=catalog_root,
                instance_id=instance_id,
                control_output=second_control,
                candidate_output=second_candidate,
                ttl_minutes=ttl_minutes,
                timeout_seconds=timeout_seconds,
                arga_candidate_safe_profile=arga_candidate_safe_profile,
            )
            controls.append(second_control)
            second_snapshot = await capture.capture(
                _read_json_object(second_control),
                roles=bundle.binding.roles,
                snapshot_queries=bundle.verification.deterministic.snapshot_queries,
            )
            independent_sha256 = stable_json_sha256(second_snapshot.artifact_payload())
            _, first_run_id = read_control_ids(first_control)
            _, second_run_id = read_control_ids(second_control)
            independent_run_proved = first_run_id != second_run_id and independent_sha256 == baseline_sha256

            if mutation_probe is not None:
                await mutation_probe(first_candidate)
                mutated_snapshot = await capture.capture(
                    _read_json_object(first_control),
                    roles=bundle.binding.roles,
                    snapshot_queries=bundle.verification.deterministic.snapshot_queries,
                )
                mutation_sha256 = stable_json_sha256(mutated_snapshot.artifact_payload())
                await reset_instance(first_control)
                post_reset = await capture.capture(
                    _read_json_object(first_control),
                    roles=bundle.binding.roles,
                    snapshot_queries=bundle.verification.deterministic.snapshot_queries,
                )
                post_probe_reset_sha256 = stable_json_sha256(post_reset.artifact_payload())
                mutation_visibility_proved = (
                    mutation_sha256 != baseline_sha256 and post_probe_reset_sha256 == baseline_sha256
                )
        except BaseException as error:
            primary_error = error
        finally:
            for control in reversed(controls):
                try:
                    _, run_id = read_control_ids(control)
                    cleanup = await cleanup_instance(control)
                    cleanup_confirmed = cleanup_confirmed and cleanup_payload_proves_inert(
                        cleanup,
                        expected_run_id=run_id,
                    )
                except BaseException as error:
                    cleanup_confirmed = False
                    if cleanup_error is None:
                        cleanup_error = error

        if primary_error is not None:
            raise primary_error
        if cleanup_error is not None:
            raise cleanup_error
        if baseline_sha256 is None:
            raise AssertionError("live conformance baseline was not captured")
        evidence = LiveLifecycleEvidence(
            protocol=LIVE_LIFECYCLE_PROTOCOL,
            instance_id=instance_id,
            instance_bundle_sha256=bundle.instance_bundle_sha256,
            verifier_sha256=bundle.verifier_sha256,
            required_reset_count=reset_count,
            baseline_state_sha256=baseline_sha256,
            reset_state_sha256=reset_hashes,
            independent_run_state_sha256=independent_sha256,
            independent_run_proved=independent_run_proved,
            mutation_visibility_proved=mutation_visibility_proved,
            mutation_probe_state_sha256=mutation_sha256,
            post_probe_reset_state_sha256=post_probe_reset_sha256,
            cleanup_confirmed=cleanup_confirmed,
        )
        write_private_json(output, evidence.model_dump(mode="json"))
        return evidence


def _read_json_object(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConformanceError(f"{path}: expected a JSON object")
    return cast(dict[str, Any], raw)
