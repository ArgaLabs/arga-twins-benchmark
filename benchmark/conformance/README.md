# Twin conformance

Conformance is a prerequisite, not an agent leaderboard track.

Each provider used by a scored task must prove deterministic reset and seed behavior, canonical-state completeness, relevant authorization and error semantics, and the API operations required by its gold and negative-control solutions.

The release gate is ten equivalent resets plus repeated gold passes for every scored instance.

## Current audit baseline

The development seeds were reviewed against `validation-server@1aa60e0768adc4dcbccf932bdf2efe93917b5007`; the lifecycle was reviewed against `arga-cli@c88d5f160343e79b1de9ba5554e856825c566edc`. This is static contract evidence, not live conformance.

For every instance, the conformance suite must:

1. Compile and save the checked-in seed without a Scenario prompt; verify its readable name, concrete task description, exact `seed_config`, and content-hash tag.
2. Provision fresh twins through `arga twin-runs create --wait --json` and require `status == ready`.
3. Read task-relevant state through ordinary provider APIs and compare it with the expected canonical baseline.
4. Reset ten times and require the same canonical baseline every time.
5. Run the gold solution repeatedly and require every critical predicate plus zero forbidden mutations.
6. Run each registered negative control and require its intended predicate to fail.
7. Verify unrelated mutations are visible to the state reader.
8. Tear down the twin run through the CLI, confirm lifecycle cleanup, and confirm the saved Scenario remains reusable.

No development instance moves into a scored split merely because its JSON shape is accepted by the seeder.
