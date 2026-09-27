"""Audio activity measurements, not perceptual quality or proof of semantic interruption."""

from argparse import ArgumentTypeError

from agent_service.calls.live_bridge import has_sound


def duration(value):
    try:
        seconds = int(value)
        if not 1 <= seconds <= 45:
            raise ValueError
    except ValueError:
        raise ArgumentTypeError("duration must be 1..45 seconds") from None
    return seconds


def packet_frames(raw, frames):
    """Compare both streams at 20ms resolution; retain actual chunk arrival times."""
    return [
        [
            stamp,
            offset,
            min(160, start + size - offset),
            has_sound(raw[offset : min(offset + 160, start + size)]),
        ]
        for stamp, start, size, _ in frames
        for offset in range(start, start + size, 160)
    ]


def silence_latency(frames, marker):
    """First 300ms low-energy audio after injection; network stalls aren't silence.

    This can be a natural pause, so assess the transcript and overlap too.
    Timestamp uncertainty includes the model's chunk duration and network jitter.
    """
    if marker is None:
        return None
    quiet_bytes, start = 0, None
    for stamp, _, size, voiced in frames:
        if stamp < marker:
            continue
        if voiced:
            quiet_bytes, start = 0, None
        else:
            if start is None:
                start = stamp
            quiet_bytes += size
            if quiet_bytes >= 2400:
                return round((start - marker) * 1000)
    return None


def voice_overlap_ms(sent, marker, clip_duration_ms):
    if marker is None:
        return None
    end = marker + clip_duration_ms / 1000
    return round(
        1000
        * sum(
            max(0, min(stamp + size / 8000, end) - max(stamp, marker))
            for stamp, _, size, voiced in sent
            if voiced
        )
    )


def summarize(model_audio, phone_audio, frames, sent, markers):
    marker = markers[0]["t"] if markers else None
    # Cumulative pending data at chunk arrival, including silence and packetization.
    index, played, maximum = 0, 0, 0
    for stamp, start, size, _ in frames:
        while index < len(sent) and sent[index][0] <= stamp:
            played = sent[index][1] + sent[index][2]
            index += 1
        maximum = max(maximum, start + size - played)
    return {
        "voice_overlap_ms": voice_overlap_ms(
            sent, marker, markers[0].get("clip_duration_ms", 0) if markers else 0
        ),
        "model_bytes": len(model_audio),
        "played_bytes": len(phone_audio),
        "prefix_identical": phone_audio == model_audio[: len(phone_audio)],
        "max_queue_ms": maximum / 8,
        "model_silence_latency_ms": silence_latency(packet_frames(model_audio, frames), marker),
        "playback_silence_latency_ms": silence_latency(sent, marker),
    }
