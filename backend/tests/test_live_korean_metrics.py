"""Regression tests for reported measurements, independent of live model output."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "live_korean_metrics", Path(__file__).resolve().parents[2] / "scripts/live_korean_metrics.py"
)
metrics = importlib.util.module_from_spec(SPEC)


def setup_module():
    SPEC.loader.exec_module(metrics)


def test_short_word_pause_is_not_reported_as_yield():
    # [receipt seconds, stream byte offset, packet size, voice activity]
    rows = [[1.0, 0, 800, True], [1.1, 800, 800, False], [1.2, 1600, 800, True]]
    assert metrics.silence_latency(rows, 1.0) is None
    rows += [[1.3, 2400, 800, False], [1.4, 3200, 800, False], [1.5, 4000, 800, False]]
    assert metrics.silence_latency(rows, 1.0) == 300


def test_transport_stall_does_not_count_as_audio_silence():
    rows = [[1.0, 0, 160, False], [3.0, 160, 160, False], [3.1, 320, 160, True]]
    assert metrics.silence_latency(rows, 1.0) is None


def test_no_interruption_cannot_be_scored():
    assert metrics.silence_latency([[1.0, 0, 8000, False]], None) is None


def test_packetize_preserves_chunk_receipt_clock_and_pcmu_voicing():
    packets = metrics.packet_frames(bytes([0] * 160 + [255] * 160), [[2.0, 0, 320, True]])
    assert packets == [[2.0, 0, 160, True], [2.0, 160, 160, False]]


def test_bounds_prevent_accidental_long_experiments():
    assert metrics.duration("42") == 42
    for value in ["0", "-1", "46", "no"]:
        with pytest.raises(Exception):
            metrics.duration(value)


def test_overlap_counts_only_voiced_samples_in_clip_window():
    rows = [
        [0.9, 0, 160, True],
        [1.0, 160, 160, True],
        [1.02, 320, 160, False],
        [1.04, 480, 160, True],
        [1.06, 640, 160, True],
    ]
    assert metrics.voice_overlap_ms(rows, 1.0, 50) == 30
