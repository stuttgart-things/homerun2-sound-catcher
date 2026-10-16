"""Generate the bundled clips in sounds/ — synthesized, so no licence questions.

    python hack/make_sounds.py
"""

import math
import struct
import wave
from pathlib import Path

RATE = 22050
OUT = Path(__file__).resolve().parents[1] / "sounds"


def tone(freq: float, seconds: float, volume: float = 0.5, decay: float = 4.0) -> list[float]:
    n = int(RATE * seconds)
    return [volume * math.sin(2 * math.pi * freq * i / RATE) * math.exp(-decay * i / n) for i in range(n)]


def silence(seconds: float) -> list[float]:
    return [0.0] * int(RATE * seconds)


def write(name: str, samples: list[float]) -> None:
    OUT.mkdir(exist_ok=True)
    with wave.open(str(OUT / name), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))


# Two-note chime, before an announcement.
write("chime.wav", tone(880, 0.35) + tone(659.25, 0.6))
# Rising three notes, for success.
write("success.wav", tone(523.25, 0.15, decay=2) + tone(659.25, 0.15, decay=2) + tone(783.99, 0.5))
# Short single ding, for warnings.
write("ding.wav", tone(1046.5, 0.5, decay=5))
# Insistent two-tone alarm, for errors.
alarm = []
for _ in range(3):
    alarm += tone(988, 0.18, 0.6, decay=0.5) + tone(740, 0.18, 0.6, decay=0.5) + silence(0.06)
write("alarm.wav", alarm)
