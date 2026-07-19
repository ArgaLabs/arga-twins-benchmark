import json
from pathlib import Path

import pytest

from arga_twins_benchmark.lifecycle import read_control_ids, write_private_json


def test_private_json_is_mode_0600(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "control.json"
    write_private_json(output, {"secret": "value"})

    assert json.loads(output.read_text()) == {"secret": "value"}
    assert oct(output.stat().st_mode & 0o777) == "0o600"


def test_control_ids_are_required(tmp_path: Path) -> None:
    control = tmp_path / "control.json"
    control.write_text('{"scenario_id": "scenario-1", "run_id": "run-1"}')
    assert read_control_ids(control) == ("scenario-1", "run-1")

    control.write_text("{}")
    with pytest.raises(ValueError, match="scenario_id"):
        read_control_ids(control)
