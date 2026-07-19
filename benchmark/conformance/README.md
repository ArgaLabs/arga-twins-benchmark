# Twin conformance

Conformance is a prerequisite, not an agent leaderboard track.

Each provider used by a scored task must prove deterministic reset and seed behavior, canonical-state completeness, relevant authorization and error semantics, and the API operations required by its gold and negative-control solutions.

The release gate is ten equivalent resets plus repeated gold passes for every scored instance.
