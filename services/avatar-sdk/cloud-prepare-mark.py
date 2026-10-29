import math
import shutil
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

root = Path.home() / "eduai-a2f"
source = root / "models/mark"
destination = root / "models/mark-runtime"
destination.mkdir(parents=True, exist_ok=True)
for pattern in ("*.json", "*.npz"):
    for path in source.glob(pattern):
        shutil.copy2(path, destination / path.name)
sys.path.insert(0, str(root / "sdk/audio2x-common/scripts"))
import audio2x.trt

audio2x.trt.convert_onnx_to_trt_from_folders(str(source), str(destination))
engine = destination / "network.trt"
if not engine.exists() or engine.stat().st_size < 1000000:
    raise RuntimeError("TensorRT conversion failed")
with wave.open(str(Path.home() / "teacher-cedar.wav"), "rb") as audio:
    if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
        raise RuntimeError("Unexpected Cedar WAV format")
    rate = audio.getframerate()
    samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
factor = math.gcd(rate, 16000)
converted = np.clip(resample_poly(samples, 16000 // factor, rate // factor), -1, 1)
with wave.open(str(root / "cedar-16k.wav"), "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(16000)
    audio.writeframes((converted * 32767).astype("<i2").tobytes())
print("EDUAI_MARK_MODEL_READY", engine.stat().st_size)
