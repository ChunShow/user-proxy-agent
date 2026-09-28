"""Build synthetic Korean ARS clips locally using macOS Yuna. No external TTS API."""

import audioop
import hashlib
import json
import subprocess
import wave
from pathlib import Path

from agent_service.settings import ROOT
from agent_service.simulator.engine import SCENARIO_TEXTS

AUDIO_DIR = ROOT / "var/simulator/audio"


def prepare(directory=AUDIO_DIR):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = hashlib.sha256(json.dumps(SCENARIO_TEXTS, ensure_ascii=False).encode()).hexdigest()
    stamp = directory / "version.txt"
    if (
        stamp.exists()
        and stamp.read_text() == digest
        and all((directory / f"{name}.ulaw").is_file() for name in SCENARIO_TEXTS)
    ):
        return directory
    for name, text in SCENARIO_TEXTS.items():
        aiff, wav = directory / f"{name}.aiff", directory / f"{name}.wav"
        subprocess.run(
            ["/usr/bin/say", "-v", "Yuna", "-r", "175", "-o", str(aiff), text],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "/usr/bin/afconvert",
                "-f",
                "WAVE",
                "-d",
                "LEI16@8000",
                "-c",
                "1",
                str(aiff),
                str(wav),
            ],
            check=True,
            capture_output=True,
        )
        with wave.open(str(wav)) as source:
            assert (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (
                1,
                2,
                8000,
            )
            raw = audioop.lin2ulaw(source.readframes(source.getnframes()), 2)
        (directory / f"{name}.ulaw").write_bytes(raw)
    stamp.write_text(digest)
    return directory


if __name__ == "__main__":
    print(f"Prepared virtual ARS audio: {prepare()}")
