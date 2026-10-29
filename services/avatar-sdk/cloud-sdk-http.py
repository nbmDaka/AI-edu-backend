import base64
import hmac
import json
import os
import queue
import secrets
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import numpy as np
import soxr

ROOT = Path.home() / "eduai-a2f"
ENGINE = "nvidia-audio2face-sdk-mark-2.3"
key_path = ROOT / "stream-key"
if not key_path.exists():
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(secrets.token_urlsafe(32))
KEY = key_path.read_text().strip()


class NativeEngine:
    def __init__(self):
        self.messages = queue.Queue(maxsize=64)
        self.process = subprocess.Popen([str(ROOT / "cloud-sdk-stream"), str(ROOT / "models/mark-runtime/model.json")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        threading.Thread(target=self.read, daemon=True).start()
        ready = self.result(60)
        if not ready.get("ready") or len(ready.get("names", [])) != 52:
            raise RuntimeError("SDK failed to initialize 52 facial channels")
        self.names = ready["names"]

    def read(self):
        for line in self.process.stdout:
            if line.startswith(b"EDUAI_JSON "):
                self.messages.put(json.loads(line[len(b"EDUAI_JSON "):]))
        self.messages.put({"error": "Native process exited"})

    def result(self, timeout=20):
        result = self.messages.get(timeout=timeout)
        if result.get("error"):
            raise RuntimeError("Native inference failed")
        return result

    def command(self, operation, samples=None):
        samples = np.asarray(samples if samples is not None else [], dtype="<f4")
        self.process.stdin.write(struct.pack("<II", operation, len(samples)) + samples.tobytes())
        self.process.stdin.flush()
        result = self.result()
        if result.get("operation") != operation:
            raise RuntimeError("Unexpected native acknowledgement")
        return result["frames"]

    def feed(self, samples):
        frames = []
        for start in range(0, len(samples), 16000):
            frames.extend(self.command(2, samples[start:start + 16000]))
        return frames


class State:
    def __init__(self):
        self.native = NativeEngine()
        self.lock = threading.Lock()
        self.session = None
        self.received = 0
        self.touched = time.monotonic()
        self.resampler = None


state = State()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, code, data):
        encoded = json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def authorized(self):
        return not self.headers.get("Origin") and hmac.compare_digest(self.headers.get("X-Stream-Key", ""), KEY)

    def do_GET(self):
        if not self.authorized():
            self.reply(403, {"error": "Forbidden"})
        elif self.path == "/health":
            ready = state.native.process.poll() is None
            self.reply(200 if ready else 503, {"ready": ready, "engine": ENGINE, "channels": 52})
        else:
            self.reply(404, {"error": "Not found"})

    def do_POST(self):
        self.connection.settimeout(10)
        if not self.authorized():
            self.close_connection = True
            self.reply(403, {"error": "Forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 72000 or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Invalid body")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
        except (ValueError, OSError):
            self.close_connection = True
            self.reply(400, {"error": "Invalid request"})
            return
        if not state.lock.acquire(timeout=2):
            self.reply(429, {"error": "Inference busy"})
            return
        try:
            if self.path == "/start":
                if state.session and time.monotonic() - state.touched <= 60:
                    self.reply(409, {"error": "A stream is already active"})
                    return
                state.native.command(1)
                state.session = uuid4().hex
                state.received = 0
                state.resampler = soxr.ResampleStream(24000, 16000, 1, dtype="float32", quality="HQ")
                state.touched = time.monotonic()
                self.reply(200, {"session": state.session, "engine": ENGINE})
            elif self.path == "/stop":
                if payload.get("reset") or payload.get("session") == state.session:
                    state.native.command(4)
                    state.session = None
                    state.resampler = None
                self.reply(200, {"stopped": True})
            elif self.path == "/chunk":
                if not state.session or payload.get("session") != state.session:
                    self.reply(410, {"error": "Stream expired"})
                    return
                pcm = payload.get("pcm")
                if not isinstance(pcm, str) or len(pcm) > 64000:
                    raise ValueError("Invalid PCM")
                raw = base64.b64decode(pcm, validate=True)
                if len(raw) % 2 or len(raw) > 48000 or state.received + len(raw) // 2 > 24000 * 90:
                    raise ValueError("Audio limit exceeded")
                final = payload.get("final", False)
                if not isinstance(final, bool):
                    raise ValueError("Invalid final flag")
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
                converted = state.resampler.resample_chunk(samples, last=final)
                frames = state.native.feed(converted)
                state.received += len(raw) // 2
                state.touched = time.monotonic()
                result = {"names": state.native.names, "frames": frames}
                if final:
                    result["frames"].extend(state.native.command(3))
                    result["duration"] = state.received / 24000
                    state.session = None
                    state.resampler = None
                self.reply(200, result)
            else:
                self.reply(404, {"error": "Not found"})
        except (ValueError, TypeError):
            self.reply(400, {"error": "Invalid audio packet"})
        except (RuntimeError, queue.Empty, OSError):
            self.reply(503, {"error": "Native inference unavailable"})
        finally:
            state.lock.release()

    def log_message(self, format, *args):
        print("A2F_HTTP", self.command, self.path, flush=True)


print("EDUAI_A2F_HTTP_READY 127.0.0.1:8012", flush=True)
try:
    ThreadingHTTPServer(("127.0.0.1", 8012), Handler).serve_forever()
finally:
    state.native.process.terminate()
