from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unicodedata
import wave
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

APP_TITLE = "Local TTS (Russian only)"

SHERPA_ONNX_TTS_EXE = os.getenv("SHERPA_ONNX_TTS_EXE", "sherpa-onnx-offline-tts")
KOKORO_MODEL_PATH = os.getenv("KOKORO_MODEL_PATH", "").strip()
KOKORO_VOICES_PATH = os.getenv("KOKORO_VOICES_PATH", "").strip()
KOKORO_TOKENS_PATH = os.getenv("KOKORO_TOKENS_PATH", "").strip()
KOKORO_DATA_DIR = os.getenv("KOKORO_DATA_DIR", "").strip()
KOKORO_LEXICON_PATHS = os.getenv("KOKORO_LEXICON_PATHS", "").strip()
KOKORO_RULE_FSTS = os.getenv("KOKORO_RULE_FSTS", "").strip()
KOKORO_NUM_THREADS = os.getenv("KOKORO_NUM_THREADS", "2").strip()
KOKORO_LENGTH_SCALE = os.getenv("KOKORO_LENGTH_SCALE", "1.0").strip()
KOKORO_PROVIDER = os.getenv("KOKORO_PROVIDER", "cpu").strip().lower()
KOKORO_CUDA_DLL_DIR = os.getenv("KOKORO_CUDA_DLL_DIR", "").strip()
TTS_MAX_TEXT_LENGTH = int(os.getenv("TTS_MAX_TEXT_LENGTH", "1500"))
TTS_DEFAULT_LANGUAGE = os.getenv("TTS_DEFAULT_LANGUAGE", "ru").strip().lower()
SERVICE_CORS_ORIGINS = os.getenv("SERVICE_CORS_ORIGINS", "*")
SERVICE_CORS_ALLOW_CREDENTIALS = (
    os.getenv("SERVICE_CORS_ALLOW_CREDENTIALS", "false").strip().lower() == "true"
)

KOKORO_SID_DEFAULT = os.getenv("KOKORO_SID_DEFAULT", "0").strip()
KOKORO_SID_EN = os.getenv("KOKORO_SID_EN", KOKORO_SID_DEFAULT).strip()
KOKORO_SID_RU = os.getenv("KOKORO_SID_RU", KOKORO_SID_DEFAULT).strip()
KOKORO_SID_KK = os.getenv("KOKORO_SID_KK", KOKORO_SID_DEFAULT).strip()

PIPER_EXE = os.getenv("PIPER_EXE", r"C:\piper\piper.exe").strip()
PIPER_MODEL_RU = os.getenv("PIPER_MODEL_RU", r"C:\piper\models\ru\denis\ru_RU-denis-medium.onnx").strip()
PIPER_MODEL_KK = os.getenv("PIPER_MODEL_KK", r"C:\piper\models\kk\kk_KZ-issai-high.onnx").strip()
PIPER_MODEL_EN = os.getenv("PIPER_MODEL_EN", r"C:\piper\models\en\en_US-lessac-medium.onnx").strip()
PIPER_LENGTH_SCALE = os.getenv("PIPER_LENGTH_SCALE", "1.04").strip()
PIPER_MODEL_RU_FALLBACKS = os.getenv(
    "PIPER_MODEL_RU_FALLBACKS",
    r"C:\piper\models\ru\denis\ru_RU-denis-medium.onnx,"
    r"C:\piper\models\ru\dmitri\ru_RU-dmitri-medium.onnx,"
    r"C:\piper\models\ru\ruslan\ru_RU-ruslan-medium.onnx",
).strip()
PIPER_MODEL_EN_FALLBACKS = os.getenv(
    "PIPER_MODEL_EN_FALLBACKS",
    r"C:\piper\models\en\en_US-lessac-medium.onnx",
).strip()
PIPER_MIXED_LANGUAGE_MODE = os.getenv("PIPER_MIXED_LANGUAGE_MODE", "single_voice").strip().lower()
EN_SWITCH_MIN_CHARS = int(os.getenv("EN_SWITCH_MIN_CHARS", "20"))
EN_SWITCH_MIN_WORDS = int(os.getenv("EN_SWITCH_MIN_WORDS", "3"))

ALLOWED_LANGUAGES = {"ru"}
LATIN_SPAN_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9_./+#-]*)(?:[ \t]+[A-Za-z][A-Za-z0-9_./+#-]*)*"
)
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
CYR_CHARS = "А-Яа-яЁёӘәІіҢңҒғҮүҰұҚқӨөҺһ"
INTER_SEGMENT_PAUSE_SEC = 0.035

# Ensure CUDA runtime DLLs are resolvable for Windows GPU sherpa-onnx builds.
if KOKORO_CUDA_DLL_DIR and Path(KOKORO_CUDA_DLL_DIR).exists():
    os.environ["PATH"] = f"{KOKORO_CUDA_DLL_DIR}{os.pathsep}{os.environ.get('PATH', '')}"

app = FastAPI(title=APP_TITLE)


def _parse_cors_origins(raw_value: str) -> list[str]:
    origins = [item.strip() for item in raw_value.split(",") if item.strip()]
    return origins or ["*"]


cors_origins = _parse_cors_origins(SERVICE_CORS_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=SERVICE_CORS_ALLOW_CREDENTIALS and cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _resolve_language(language: str) -> str:
    lang = (language or TTS_DEFAULT_LANGUAGE).strip().lower()
    if lang not in ALLOWED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    return lang


def _slash_words(language: str) -> tuple[str, str]:
    return "или", "и"


def _normalize_text_for_tts(text: str, language: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return normalized

    urls: list[str] = []

    def _stash_url(match: re.Match[str]) -> str:
        urls.append(match.group(0))
        return f" __URL_{len(urls) - 1}__ "

    normalized = URL_RE.sub(_stash_url, normalized)

    sep_or, sep_and = _slash_words(language)

    # Cases like: "спам / не спам" -> "спам или не спам"
    normalized = re.sub(r"(?<=\S)\s*/\s*(?=\S)", f" {sep_or} ", normalized)
    # Cases like: "Python/Scikit-learn" -> "Python и Scikit-learn"
    normalized = re.sub(r"(?<=[A-Za-z])/(?=[A-Za-z])", f" {sep_and} ", normalized)
    # Cases like: "класс/группа" -> "класс или группа"
    normalized = re.sub(
        rf"(?<=[{CYR_CHARS}])/(?=[{CYR_CHARS}])",
        f" {sep_or} ",
        normalized,
    )

    # Keep list readability in speech and respect line breaks as pauses.
    normalized = normalized.replace("```", "")
    normalized = re.sub(r"\*\*([^*]+)\*\*", r"\1", normalized)
    normalized = re.sub(r"__([^_]+)__", r"\1", normalized)
    normalized = re.sub(r"\*([^*\n]+)\*", r"\1", normalized)
    normalized = re.sub(r"_([^_\n]+)_", r"\1", normalized)
    normalized = normalized.replace("*", "")
    normalized = re.sub(r"(?m)^\s*[-*•]\s+", "", normalized)
    normalized = re.sub(r"(?m)^\s*\d+[.)]\s+", "", normalized)
    normalized = re.sub(r"\n{2,}", " ... ", normalized)
    normalized = re.sub(r"\n", "; ", normalized)

    normalized = re.sub(r"[ \t]{2,}", " ", normalized).strip()

    for index, url in enumerate(urls):
        normalized = normalized.replace(f"__URL_{index}__", url)

    return normalized


def _normalize_text_for_kokoro(text: str) -> str:
    # On Windows some sherpa-onnx builds parse argv as non-UTF8 bytes.
    # Keep text in a conservative unicode form to avoid tokenization failures.
    normalized = unicodedata.normalize("NFKC", text)
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201A": "'",
        "\u201B": "'",
        "\u2032": "'",
        "\u02BC": "'",
        "\u201C": '"',
        "\u201D": '"',
        "\u201E": '"',
        "\u201F": '"',
        "\u2033": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2026": "...",
        "\u00A0": " ",
        "\u2007": " ",
        "\u202F": " ",
        "\u200B": "",
        "\u200C": "",
        "\u200D": "",
        "\uFEFF": "",
        "\uFFFD": "'",
    }
    for src, dst in replacements.items():
        normalized = normalized.replace(src, dst)

    normalized = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", " ", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized).strip()
    return normalized


def _split_csv_paths(csv_value: str) -> list[str]:
    return [item.strip() for item in csv_value.split(",") if item.strip()]


def _resolve_sid(language: str) -> str:
    mapping = {
        "ru": KOKORO_SID_RU,
        "kk": KOKORO_SID_KK,
        "en": KOKORO_SID_EN,
    }
    sid = mapping.get(language) or KOKORO_SID_DEFAULT
    if not sid:
        sid = "0"
    return sid


def _validate_static_config() -> None:
    if not KOKORO_MODEL_PATH:
        raise HTTPException(status_code=500, detail="KOKORO_MODEL_PATH is not configured")
    if not Path(KOKORO_MODEL_PATH).exists():
        raise HTTPException(status_code=500, detail=f"Model file not found: {KOKORO_MODEL_PATH}")

    if not KOKORO_VOICES_PATH:
        raise HTTPException(status_code=500, detail="KOKORO_VOICES_PATH is not configured")
    if not Path(KOKORO_VOICES_PATH).exists():
        raise HTTPException(status_code=500, detail=f"Voices file not found: {KOKORO_VOICES_PATH}")

    if not KOKORO_TOKENS_PATH:
        raise HTTPException(status_code=500, detail="KOKORO_TOKENS_PATH is not configured")
    if not Path(KOKORO_TOKENS_PATH).exists():
        raise HTTPException(status_code=500, detail=f"Tokens file not found: {KOKORO_TOKENS_PATH}")

    if not KOKORO_DATA_DIR:
        raise HTTPException(status_code=500, detail="KOKORO_DATA_DIR is not configured")
    if not Path(KOKORO_DATA_DIR).exists():
        raise HTTPException(status_code=500, detail=f"Data dir not found: {KOKORO_DATA_DIR}")

    lexicons = _split_csv_paths(KOKORO_LEXICON_PATHS)
    if not lexicons:
        raise HTTPException(status_code=500, detail="KOKORO_LEXICON_PATHS is not configured")

    for lexicon in lexicons:
        if not Path(lexicon).exists():
            raise HTTPException(status_code=500, detail=f"Lexicon file not found: {lexicon}")

    for fst_path in _split_csv_paths(KOKORO_RULE_FSTS):
        if not Path(fst_path).exists():
            raise HTTPException(status_code=500, detail=f"Rule FST file not found: {fst_path}")


def _piper_model_for_language(language: str) -> str:
    mapping = {
        "ru": PIPER_MODEL_RU,
        "kk": PIPER_MODEL_KK,
        "en": PIPER_MODEL_EN,
    }
    return mapping.get(language, "").strip()


def _candidate_piper_models(language: str) -> list[str]:
    primary = _piper_model_for_language(language)
    candidates: list[str] = []
    if primary:
        candidates.append(primary)

    if language == "ru":
        for item in _split_csv_paths(PIPER_MODEL_RU_FALLBACKS):
            if item not in candidates:
                candidates.append(item)
    if language == "en":
        for item in _split_csv_paths(PIPER_MODEL_EN_FALLBACKS):
            if item not in candidates:
                candidates.append(item)

    return candidates


def _run_piper(command: list[str], text: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            command,
            input=text.encode("utf-8"),
            capture_output=True,
            check=True,
        )
        _ = proc  # keep for future debug extension
        return True, ""
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace").strip()
        details = stderr or stdout or "no stderr/stdout"
        return False, f"exit={exc.returncode}; {details}"


def _synthesize_with_piper(text: str, language: str, output_file: Path) -> None:
    model_candidates = _candidate_piper_models(language)
    if not model_candidates:
        raise HTTPException(
            status_code=500,
            detail=f"Piper model is not configured for language: {language}",
        )

    attempt_errors: list[str] = []
    for model_path in model_candidates:
        if not Path(model_path).exists():
            attempt_errors.append(f"{model_path}: model file not found")
            continue

        command = [
            PIPER_EXE,
            "--model",
            model_path,
            "--output_file",
            str(output_file),
        ]
        if PIPER_LENGTH_SCALE:
            command.extend(["--length_scale", PIPER_LENGTH_SCALE])

        try:
            ok, details = _run_piper(command=command, text=text)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=f"Piper executable not found: {PIPER_EXE}") from exc

        if ok and output_file.exists() and output_file.stat().st_size > 44:
            return

        if not details:
            details = "empty output or zero-length wav"
        attempt_errors.append(f"{model_path}: {details}")

    joined_errors = " | ".join(attempt_errors) if attempt_errors else "unknown error"
    raise HTTPException(status_code=500, detail=f"Piper synthesis failed: {joined_errors}")


def _split_mixed_language_segments(text: str, base_language: str) -> list[tuple[str, str]]:
    if PIPER_MIXED_LANGUAGE_MODE != "mixed":
        return [(base_language, text)]

    if base_language not in {"ru", "kk"}:
        return [(base_language, text)]

    segments: list[tuple[str, str]] = []
    position = 0

    for match in LATIN_SPAN_RE.finditer(text):
        start, end = match.span()
        if start > position:
            left = text[position:start]
            if left:
                segments.append((base_language, left))

        english_span = text[start:end]
        english_words = re.findall(r"[A-Za-z][A-Za-z0-9_./+#-]*", english_span)
        is_long_en = len(english_span.strip()) >= EN_SWITCH_MIN_CHARS
        has_en_phrase = len(english_words) >= EN_SWITCH_MIN_WORDS

        if english_span and (is_long_en or has_en_phrase):
            segments.append(("en", english_span))
        elif english_span:
            segments.append((base_language, english_span))
        position = end

    if position < len(text):
        right = text[position:]
        if right:
            segments.append((base_language, right))

    if not segments:
        return [(base_language, text)]

    merged: list[tuple[str, str]] = []
    for language, chunk in segments:
        if not chunk:
            continue
        if merged and merged[-1][0] == language:
            merged[-1] = (language, f"{merged[-1][1]}{chunk}")
        else:
            merged.append((language, chunk))

    return merged


def _read_wav(path: Path) -> tuple[tuple[int, int, int, int], bytes]:
    with wave.open(str(path), "rb") as wf:
        params = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getcomptype() == "NONE")
        data = wf.readframes(wf.getnframes())
    return params, data


def _merge_wavs(input_paths: list[Path], output_path: Path) -> None:
    if not input_paths:
        raise HTTPException(status_code=500, detail="No synthesized chunks to merge")

    first_params, first_data = _read_wav(input_paths[0])
    target_channels, target_sampwidth, target_rate, target_is_pcm = first_params
    if not target_is_pcm:
        raise HTTPException(status_code=500, detail="Only PCM WAV is supported for merged TTS")

    frame_size = target_channels * target_sampwidth
    pause_frames = int(target_rate * INTER_SEGMENT_PAUSE_SEC)
    pause = b"\x00" * max(frame_size * pause_frames, 0)

    merged_parts: list[bytes] = [first_data]

    for chunk_path in input_paths[1:]:
        params, data = _read_wav(chunk_path)
        src_channels, src_sampwidth, src_rate, src_is_pcm = params
        if not src_is_pcm:
            raise HTTPException(status_code=500, detail="Non-PCM chunk in TTS merge")
        if (
            src_channels != target_channels
            or src_sampwidth != target_sampwidth
            or src_rate != target_rate
        ):
            raise HTTPException(
                status_code=500,
                detail=(
                    "Cannot merge mixed-language audio chunks due to different WAV params: "
                    f"expected {target_channels}ch/{target_sampwidth * 8}bit/{target_rate}Hz, "
                    f"got {src_channels}ch/{src_sampwidth * 8}bit/{src_rate}Hz"
                ),
            )
        if pause:
            merged_parts.append(pause)
        merged_parts.append(data)

    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(target_channels)
        wf.setsampwidth(target_sampwidth)
        wf.setframerate(target_rate)
        wf.writeframes(b"".join(merged_parts))


def _synthesize_with_piper_mixed(text: str, base_language: str, output_file: Path) -> None:
    segments = _split_mixed_language_segments(text=text, base_language=base_language)
    if len(segments) == 1:
        _synthesize_with_piper(text=text, language=base_language, output_file=output_file)
        return

    chunk_paths: list[Path] = []
    try:
        for segment_language, segment_text in segments:
            segment = segment_text.strip()
            if not segment:
                continue

            effective_language = segment_language
            if segment_language == "en" and not _candidate_piper_models("en"):
                effective_language = base_language

            tmp_chunk = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            chunk_path = Path(tmp_chunk.name)
            tmp_chunk.close()

            _synthesize_with_piper(
                text=segment,
                language=effective_language,
                output_file=chunk_path,
            )
            chunk_paths.append(chunk_path)

        if not chunk_paths:
            raise HTTPException(status_code=500, detail="No TTS chunks were synthesized")

        _merge_wavs(input_paths=chunk_paths, output_path=output_file)
    finally:
        for chunk in chunk_paths:
            chunk.unlink(missing_ok=True)


def _synthesize_with_kokoro(text: str, language: str, output_file: Path) -> None:
    _validate_static_config()
    sid = _resolve_sid(language)
    provider = KOKORO_PROVIDER if KOKORO_PROVIDER in {"cpu", "cuda", "coreml"} else "cpu"
    safe_text = _normalize_text_for_kokoro(text)
    if not safe_text:
        raise HTTPException(status_code=400, detail="Empty text after Kokoro normalization")

    def _build_command(current_provider: str) -> list[str]:
        command = [
            SHERPA_ONNX_TTS_EXE,
            "--debug=0",
            f"--provider={current_provider}",
            f"--kokoro-model={KOKORO_MODEL_PATH}",
            f"--kokoro-voices={KOKORO_VOICES_PATH}",
            f"--kokoro-tokens={KOKORO_TOKENS_PATH}",
            f"--kokoro-data-dir={KOKORO_DATA_DIR}",
            f"--kokoro-lexicon={KOKORO_LEXICON_PATHS}",
            f"--num-threads={KOKORO_NUM_THREADS}",
            f"--sid={sid}",
            f"--kokoro-length-scale={KOKORO_LENGTH_SCALE}",
            f"--output-filename={output_file}",
            safe_text,
        ]
        if KOKORO_RULE_FSTS:
            command.insert(-2, f"--tts-rule-fsts={KOKORO_RULE_FSTS}")
        return command

    def _run_once(current_provider: str) -> tuple[bool, str]:
        command = _build_command(current_provider)
        try:
            subprocess.run(
                command,
                capture_output=True,
                check=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return True, ""
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"sherpa-onnx executable not found: {SHERPA_ONNX_TTS_EXE}",
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or f"exit={exc.returncode}"
            return False, details

    ok, details = _run_once(provider)
    if ok:
        return

    can_retry_cpu = provider != "cpu" and (
        "SHERPA_ONNX_ENABLE_GPU" in details
        or "Available providers" in details
        or "Fallback to cpu" in details
    )

    if can_retry_cpu:
        ok_cpu, details_cpu = _run_once("cpu")
        if ok_cpu:
            return
        raise HTTPException(
            status_code=500,
            detail=(
                "sherpa-onnx synthesis failed with GPU provider and CPU fallback. "
                f"GPU error: {details} | CPU error: {details_cpu}"
            ),
        )

    raise HTTPException(status_code=500, detail=f"sherpa-onnx synthesis failed: {details}")


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=TTS_MAX_TEXT_LENGTH)
    language: str = Field(default=TTS_DEFAULT_LANGUAGE)


@app.get("/health")
def health() -> dict[str, str]:
    piper_ru_ok = bool(PIPER_MODEL_RU and Path(PIPER_MODEL_RU).exists())
    return {
        "status": "ok",
        "engine": "piper(ru)",
        "exe": PIPER_EXE,
        "default_language": "ru",
        "piper_ru_ready": str(piper_ru_ok).lower(),
        "piper_model_ru": PIPER_MODEL_RU,
    }


@app.post("/synthesize")
def synthesize(payload: SynthesizeRequest):
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")

    language = _resolve_language(payload.language)
    text_for_tts = _normalize_text_for_tts(text=text, language=language)
    if not text_for_tts:
        raise HTTPException(status_code=400, detail="Empty text after normalization")

    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp_path = Path(tmp_file.name)
    tmp_file.close()

    try:
        _synthesize_with_piper(
            text=text_for_tts,
            language="ru",
            output_file=tmp_path,
        )
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"TTS failed: {exc}") from exc

    return FileResponse(
        path=str(tmp_path),
        media_type="audio/wav",
        filename="speech.wav",
        background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)),
    )
