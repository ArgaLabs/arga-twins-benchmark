from enum import StrEnum


class EpisodeState(StrEnum):
    PLANNED = "planned"
    MANIFEST_VALIDATED = "manifest_validated"
    SCENARIO_REGISTERED = "scenario_registered"
    SANDBOX_REQUESTED = "sandbox_requested"
    DEPLOYMENT_READY = "deployment_ready"
    SEED_CONFIRMED = "seed_confirmed"
    BASELINE_CAPTURED = "baseline_captured"
    INVOCATION_STARTED = "invocation_started"
    INVOCATION_FINISHED = "invocation_finished"
    FINAL_CAPTURED = "final_captured"
    GRADED = "graded"
    ARTIFACTS_COMMITTED = "artifacts_committed"
    TEARDOWN_REQUESTED = "teardown_requested"
    COMPLETE = "complete"
