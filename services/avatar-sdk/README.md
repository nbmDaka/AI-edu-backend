# NVIDIA SDK Worker Source

Our transport and native adapter around the separately installed NVIDIA
Audio2Face-3D SDK, tested on Linux, CUDA 12.9, TensorRT 10.13.3.9 and Tesla T4.
The vendor SDK source used for the test is commit
`1ca0f02535ed774f5dbcd724a31cd486368dc783` from
https://github.com/NVIDIA/Audio2Face-3D-SDK . Review its current requirements and
the separate neural-model license before installing or redistributing anything.
No vendor binaries, weights, credentials or character model are bundled here.

`cloud-sdk-stream.cpp` owns one warm native inference session. Its framed stdin
protocol resets, feeds float32 audio, flushes and stops. `cloud-sdk-http.py`
accepts private PCM16 at 24 kHz, statefully resamples to 16 kHz and returns 52
named facial channels with sample-based timestamps. The native model, not an
audio-amplitude approximation, computes articulation. It binds only to loopback,
requires a private token, rejects browser Origin and allows one bounded session.

Copy these adapter sources to the worker user's home, with the separately
installed SDK under `~/eduai-a2f/sdk` and the prepared model under
`~/eduai-a2f/models/mark-runtime`. Build with `cloud-build-stream.sh` after the SDK
and target-specific TensorRT engine are ready. Use an isolated Python environment
with numpy, scipy and soxr compatible with that SDK, not the DLVM system Python.

`cloud-download-mark.py` records the model repository revision and SHA256 hashes.
`cloud-prepare-mark.py` uses NVIDIA's conversion helper and builds the TensorRT
engine for the current target. Engines are hardware/version specific. The T4
test used SM75; do not reuse that engine blindly on another GPU.

Run the HTTP adapter as an unprivileged supervised service with a writable private
state directory. Expose it to the VPS only through the restricted SSH tunnel
described in `../avatar-gateway/README.md`. Runtime installation and cloud creation
are intentionally not automatic, to avoid creating billable resources by accident.
