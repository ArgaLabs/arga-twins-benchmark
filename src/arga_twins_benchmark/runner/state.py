from enum import StrEnum


class EpisodeState(StrEnum):
    PLANNED = "planned"
    MANIFEST_VALIDATED = "manifest_validated"
    SCENARIO_REGISTERED = "scenario_registered"
    TWIN_RUN_REQUESTED = "twin_run_requested"
    TWINS_READY_AND_SEEDED = "twins_ready_and_seeded"
    BASELINE_CAPTURED = "baseline_captured"
    INVOCATION_STARTED = "invocation_started"
    INVOCATION_FINISHED = "invocation_finished"
    FINAL_CAPTURED = "final_captured"
    GRADED = "graded"
    ARTIFACTS_COMMITTED = "artifacts_committed"
    TEARDOWN_REQUESTED = "teardown_requested"
    COMPLETE = "complete"
