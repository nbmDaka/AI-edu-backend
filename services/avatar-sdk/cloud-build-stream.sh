#!/usr/bin/env bash
set -euo pipefail
ROOT="$HOME/eduai-a2f"
SDK="$ROOT/sdk"
AUDIO_INCLUDE=$(dirname "$(find -L "$SDK/_deps/target-deps/audiofile" -name AudioFile.h -print -quit)")
JSON_INCLUDE=$(dirname "$(dirname "$(find -L "$SDK/_deps/target-deps/nlohmann_json" -name json.hpp -print -quit)")")
LIBDIR="$SDK/_build/audio2x-sdk/lib"
g++ -std=c++17 -O2 -pthread "$HOME/cloud-sdk-stream.cpp" -o "$ROOT/cloud-sdk-stream" \
  -I"$SDK/audio2face-sdk/include" -I"$SDK/audio2x-common/include" -I"$AUDIO_INCLUDE" -I"$JSON_INCLUDE" \
  -I/usr/local/cuda-12.9/include -L"$LIBDIR" -laudio2x -Wl,-rpath,"$LIBDIR"
echo EDUAI_A2F_STREAM_BUILT
