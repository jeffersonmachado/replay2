from __future__ import annotations

import base64
import json
from pathlib import Path


def test_raw_fixture_events_use_base64_lengths_and_no_prematerialized_payloads():
    fixture = json.loads(Path("tests/fixtures/capture8_replay_fixture.json").read_text(encoding="utf-8"))
    events = fixture["events"]
    assert fixture["format"] == "dakota.capture.audit.raw.v1"
    assert "replay_events" not in fixture
    assert "timeline" not in fixture
    assert "playback" not in fixture
    assert all("replay_events" not in ev and "timeline" not in ev and "playback" not in ev for ev in events)
    for ev in events:
        if "data_b64" in ev:
            assert ev["n"] == len(base64.b64decode(ev["data_b64"]))
