import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "live_dialogue_cases", Path(__file__).resolve().parents[3] / "scripts/live_dialogue_cases.py"
)
cases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cases)


def test_empty_or_partial_audio_is_not_complete():
    result = cases.coverage("activity_reason", [], [], [])
    assert not result["all_inputs_sent"]
    assert not result["all_inputs_transcribed"]
    assert not result["all_turns_answered"]
    assert result["semantic_review_required"]


def test_fragments_keep_original_text_and_audio_turn_boundary():
    rows = cases.join_transcripts(
        [
            {"type": "session.input_transcript.delta", "turn": 0, "delta": "컴"},
            {"type": "session.input_transcript.delta", "turn": 0, "delta": "퓨터"},
            {"type": "session.output_transcript.delta", "turn": 0, "delta": "네"},
            {"type": "session.output_transcript.delta", "turn": 1, "delta": "이유는"},
        ]
    )
    assert [r["text"] for r in rows] == ["컴퓨터", "네", "이유는"]
    assert rows[-1]["turn"] == 1


def test_complete_transcripts_still_do_not_override_transport_failure():
    rows = [{"role": role, "turn": 0, "text": "내용"} for role in ["caller", "assistant"]]
    result = cases.coverage("refusal", [{"finished": True}], rows, ["TimeoutError"])
    assert result["all_inputs_sent"] and result["all_turns_answered"]
    assert not result["transport_ok"]


def test_timeout_injection_is_not_a_natural_completed_exchange():
    rows = [{"role": role, "turn": 0, "text": "내용"} for role in ["caller", "assistant"]]
    result = cases.coverage("refusal", [{"finished": True, "forced_after_timeout": True}], rows, [])
    assert result["all_inputs_transcribed"] and result["all_turns_answered"]
    assert not result["unforced_inputs"]
