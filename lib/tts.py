"""TTS 생성: 한국어(Supertonic on-device, 기본) + 영어(Kokoro). 단락별 wav + 문장 단위 타임스탬프 반환.

2026-05-19: Edge TTS → Supertonic 마이그(팟캐스트와 동일 백엔드 통일).
환경변수 USE_EDGE_TTS=1 로 폴백 가능."""
import asyncio
import json
import os
import re
import subprocess
import tempfile
import numpy as np
import soundfile as sf
from pathlib import Path

EDGE_KO_VOICE = "ko-KR-InJoonNeural"   # Edge fallback only
EDGE_KO_RATE  = "+0%"
SUPERTONIC_VOICE = os.environ.get("SUPERTONIC_VOICE", "M1")  # 영상 뉴스 화자 (상현). 카테고리별 분기 가능
KOKORO_EN_VOICE = "af_heart"
SILENCE_PARA_SEC = 0.4    # 단락 사이 무음
SILENCE_SENT_SEC = 0.15   # 문장 사이 무음
SAMPLE_RATE = 24000

USE_SUPERTONIC = os.environ.get("USE_EDGE_TTS", "0") != "1"
_supertonic_tts = None
_supertonic_style_cache = None

EDGE_RETRY_BACKOFFS = [5, 15, 45]  # 503 등 일시 장애 재시도 (초)
ZONOS_VENV_PY = "/home/sddari/tts_eval/venv_zonos/bin/python"
ZONOS_RUNNER  = "/home/sddari/news_video_poc/lib/zonos_run.py"
ZONOS_FALLBACK_ENABLED = os.environ.get("ZONOS_FALLBACK", "0") == "1"  # 기본 비활성 (음성 품질 미충족)
EDGE_GLOBAL_LOCK = "/tmp/sosig_edge_tts.lock"  # 팟캐스트와 공유 (동일 IP rate-limit 회피)


def _supertonic_load():
    global _supertonic_tts, _supertonic_style_cache
    if _supertonic_tts is None:
        from supertonic import TTS
        _supertonic_tts = TTS(auto_download=True)
        _supertonic_style_cache = _supertonic_tts.get_voice_style(voice_name=SUPERTONIC_VOICE)
    return _supertonic_tts, _supertonic_style_cache


def _supertonic_sentence_pcm(text: str) -> np.ndarray:
    """Supertonic으로 한 문장 → SAMPLE_RATE Hz mono float32 PCM."""
    tts, style = _supertonic_load()
    with tempfile.TemporaryDirectory(prefix="st_") as tmp:
        wav, _dur = tts.synthesize(text=text, lang="ko", voice_style=style, total_steps=8, speed=1.05)
        wav_path = os.path.join(tmp, "out.wav")
        tts.save_audio(wav, wav_path)
        # 24000Hz mono로 리샘플 (Supertonic은 44100Hz)
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
             "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "f32le", "-"],
            capture_output=True,
        )
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _split_sentences_ko(text: str) -> list[str]:
    parts = re.split(r'(?<=[다요까지함음니계오아어이세죠나래라])\.\s+', text)
    parts = [p.strip() for p in parts if p.strip()]
    return [p if p.endswith(('.', '!', '?')) else p + '.' for p in parts]


def _split_sentences_en(text: str) -> list[str]:
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()]


# ── 한국어: Edge TTS ────────────────────────────────────────────────────────────

def _mp3_to_pcm(mp3_path: str) -> np.ndarray:
    """MP3 → float32 PCM (SAMPLE_RATE Hz, mono)."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", mp3_path,
         "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "f32le", "-"],
        capture_output=True,
    )
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


async def _edge_sentence_async(text: str, out_mp3: str) -> None:
    """Edge TTS 단일 문장 + 공유 lock(/tmp/sosig_edge_tts.lock) + 503 재시도(3회, 5/15/45s)."""
    import edge_tts
    import fcntl
    last_err: Exception | None = None
    # 공유 lock으로 팟캐스트와 직렬화 (Microsoft IP rate limit 회피)
    with open(EDGE_GLOBAL_LOCK, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        for attempt in range(len(EDGE_RETRY_BACKOFFS) + 1):
            try:
                comm = edge_tts.Communicate(text, EDGE_KO_VOICE, rate=EDGE_KO_RATE)
                await comm.save(out_mp3)
                return
            except Exception as e:
                last_err = e
                if attempt < len(EDGE_RETRY_BACKOFFS):
                    wait = EDGE_RETRY_BACKOFFS[attempt]
                    print(f"    [Edge TTS] {type(e).__name__} 시도 {attempt+1}/{len(EDGE_RETRY_BACKOFFS)+1} 실패, {wait}s 후 재시도", flush=True)
                    await asyncio.sleep(wait)
    raise last_err  # type: ignore[misc]


def _zonos_paragraph_fallback(sentences: list[str], tag: str) -> list[np.ndarray]:
    """Edge TTS 단락 통째 실패 시 Zonos venv subprocess 호출.
    반환: 문장별 PCM array 리스트 (SAMPLE_RATE Hz mono)."""
    with tempfile.TemporaryDirectory(prefix=f"zonos_{tag}_") as tmp:
        spec = json.dumps({"sentences": sentences, "out_dir": tmp})
        print(f"    [Zonos 폴백] {len(sentences)}문장 호출 중 (모델 로드 ~5s + 문장당 ~9s)...", flush=True)
        result = subprocess.run(
            [ZONOS_VENV_PY, ZONOS_RUNNER],
            input=spec.encode(),
            capture_output=True,
            timeout=60 + 30 * len(sentences),
        )
        if result.returncode != 0:
            raise RuntimeError(f"Zonos failed (rc={result.returncode}): {result.stderr.decode()[:500]}")
        meta = json.loads(result.stdout.decode().strip().splitlines()[-1])
        wav_paths = meta["paths"]

        pcms: list[np.ndarray] = []
        for wp in wav_paths:
            ff = subprocess.run(
                ["ffmpeg", "-y", "-i", wp, "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "f32le", "-"],
                capture_output=True,
            )
            pcms.append(np.frombuffer(ff.stdout, dtype=np.float32).copy())
        return pcms


def _edge_generate_paragraph(text: str, fallback_tag: str = "p") -> tuple[np.ndarray, list[dict]]:
    """단락 → (PCM numpy, 문장별 타임스탬프).
    기본: Supertonic on-device. USE_EDGE_TTS=1이면 Edge TTS 폴백.
    """
    sentences = _split_sentences_ko(text)
    inter_silence = np.zeros(int(SAMPLE_RATE * SILENCE_SENT_SEC), dtype=np.float32)
    chunks = []
    sent_times = []
    cursor = 0.0

    sentence_pcms: list[np.ndarray] | None = None

    if USE_SUPERTONIC:
        try:
            sentence_pcms = [_supertonic_sentence_pcm(sent) for sent in sentences]
        except Exception as e:
            print(f"    [Supertonic 실패] {type(e).__name__} → Edge TTS 시도", flush=True)
            sentence_pcms = None

    if sentence_pcms is None:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                sentence_pcms = []
                for i, sent in enumerate(sentences):
                    mp3_path = os.path.join(tmp, f"s{i}.mp3")
                    asyncio.run(_edge_sentence_async(sent, mp3_path))
                    sentence_pcms.append(_mp3_to_pcm(mp3_path))
        except Exception as e:
            if ZONOS_FALLBACK_ENABLED:
                print(f"    [Edge TTS 단락 실패] {type(e).__name__} → Zonos 폴백 시도", flush=True)
                sentence_pcms = _zonos_paragraph_fallback(sentences, fallback_tag)
            else:
                print(f"    [TTS 단락 실패] {type(e).__name__} — 폴백 비활성, 작업 중단", flush=True)
                raise

    for i, (sent, audio) in enumerate(zip(sentences, sentence_pcms)):
        dur = len(audio) / SAMPLE_RATE
        sent_times.append({"text": sent, "start": cursor, "end": cursor + dur})
        chunks.append(audio)
        cursor += dur
        if i < len(sentences) - 1:
            chunks.append(inter_silence)
            cursor += SILENCE_SENT_SEC

    full = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
    return full, sent_times


def generate_ko(narration_path: str, out_dir: str) -> list[dict]:
    """한국어 단락별 wav 생성 (Edge TTS). segments에 sentences 필드(절대시간) 포함."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    text = Path(narration_path).read_text(encoding="utf-8")
    paragraphs = _split_paragraphs(text)
    silence = np.zeros(int(SAMPLE_RATE * SILENCE_PARA_SEC), dtype=np.float32)

    segments = []
    combined = np.array([], dtype=np.float32)
    cursor = 0.0

    for i, para in enumerate(paragraphs):
        print(f"  [KO TTS {i+1}/{len(paragraphs)}] {para[:50]}...")
        audio, rel_sentences = _edge_generate_paragraph(para, fallback_tag=f"p{i:02d}")
        duration = len(audio) / SAMPLE_RATE
        seg_path = os.path.join(out_dir, f"seg_{i:02d}.wav")
        sf.write(seg_path, audio, SAMPLE_RATE)

        start = cursor
        end = cursor + duration
        abs_sentences = [
            {"text": s["text"], "start": start + s["start"], "end": start + s["end"]}
            for s in rel_sentences
        ]
        segments.append({
            "idx": i, "text": para, "start": start, "end": end,
            "wav": seg_path, "sentences": abs_sentences,
        })
        combined = np.concatenate([combined, audio, silence])
        cursor = end + SILENCE_PARA_SEC

    wav_out = os.path.join(out_dir, "audio.wav")
    sf.write(wav_out, combined, SAMPLE_RATE)
    print(f"  → {wav_out} ({len(combined)/SAMPLE_RATE:.1f}초, {len(paragraphs)}단락)")
    return segments


# ── 영어: Kokoro ────────────────────────────────────────────────────────────────

def _kokoro_generate_paragraph(text: str, pipeline) -> tuple[np.ndarray, list[dict]]:
    """단락 → (오디오, 문장별 timings). 문장 단위로 Kokoro 호출."""
    sentences = _split_sentences_en(text)
    inter_silence = np.zeros(int(SAMPLE_RATE * SILENCE_SENT_SEC), dtype=np.float32)
    chunks = []
    sent_times = []
    cursor = 0.0
    for i, sent in enumerate(sentences):
        sub_chunks = []
        for _, _, audio in pipeline(sent, voice=KOKORO_EN_VOICE, speed=1.0):
            sub_chunks.append(audio)
        if not sub_chunks:
            continue
        sent_audio = np.concatenate(sub_chunks).astype(np.float32)
        dur = len(sent_audio) / SAMPLE_RATE
        sent_times.append({"text": sent, "start": cursor, "end": cursor + dur})
        chunks.append(sent_audio)
        cursor += dur
        if i < len(sentences) - 1:
            chunks.append(inter_silence)
            cursor += SILENCE_SENT_SEC
    full = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
    return full, sent_times


def generate_en(narration_path: str, out_dir: str) -> list[dict]:
    from kokoro import KPipeline
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    text = Path(narration_path).read_text(encoding="utf-8")
    paragraphs = _split_paragraphs(text)
    sr = SAMPLE_RATE
    silence = np.zeros(int(sr * SILENCE_PARA_SEC), dtype=np.float32)

    pipe = KPipeline(lang_code='a', device='cpu')
    segments = []
    combined = np.array([], dtype=np.float32)
    cursor = 0.0

    for i, para in enumerate(paragraphs):
        print(f"  [EN TTS {i+1}/{len(paragraphs)}] {para[:50]}...")
        audio, rel_sentences = _kokoro_generate_paragraph(para, pipe)
        duration = len(audio) / sr
        seg_path = os.path.join(out_dir, f"seg_{i:02d}.wav")
        sf.write(seg_path, audio, sr)

        start = cursor
        end = cursor + duration
        abs_sentences = [
            {"text": s["text"], "start": start + s["start"], "end": start + s["end"]}
            for s in rel_sentences
        ]
        segments.append({
            "idx": i, "text": para, "start": start, "end": end,
            "wav": seg_path, "sentences": abs_sentences,
        })
        combined = np.concatenate([combined, audio, silence])
        cursor = end + SILENCE_PARA_SEC

    wav_out = os.path.join(out_dir, "audio.wav")
    sf.write(wav_out, combined, sr)

    mp3_out = os.path.join(out_dir, "audio.mp3")
    os.system(f'ffmpeg -y -i "{wav_out}" -q:a 2 "{mp3_out}" -loglevel error')
    print(f"  → {mp3_out} ({len(combined)/sr:.1f}초, {len(paragraphs)}단락)")
    return segments


if __name__ == "__main__":
    import json
    segs = generate_en("work/script_narration_en.md", "work/en")
    print(json.dumps([{k: v for k, v in s.items() if k != 'wav'} for s in segs], indent=2, ensure_ascii=False))
