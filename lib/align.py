"""scenes.json의 문장 타임스탬프 → SRT (원본 텍스트 그대로, Whisper 미사용)."""
from pathlib import Path


def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def _chunk_sentence(sentence: str, lang: str, max_chars: int) -> list[str]:
    """긴 문장을 max_chars 이하 블록으로 분할. 자연스러운 경계(쉼표, 공백) 우선."""
    if len(sentence) <= max_chars:
        return [sentence]

    blocks = []
    remaining = sentence
    while len(remaining) > max_chars:
        # max_chars 이내에서 마지막 쉼표 또는 공백 찾기
        cut = -1
        for sep in [", ", " "]:
            pos = remaining.rfind(sep, 0, max_chars + 1)
            if pos > max_chars * 0.5:
                cut = pos + (len(sep) if sep == ", " else 1)
                break
        if cut == -1:
            cut = max_chars
        blocks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        blocks.append(remaining)
    return blocks


def _build_blocks(segments: list[dict], lang: str) -> list[dict]:
    """TTS segments → 자막 블록 리스트 [{start, end, text}].
    문장 길면 자연 경계로 분할 후 시간 비례 분배.
    블록 사이 공백(>0.05초)은 다음 블록 start까지 end 연장.
    """
    max_chars = 25 if lang == "ko" else 42
    blocks = []

    for seg in segments:
        for sent in seg.get("sentences", []):
            text = sent["text"]
            start = sent["start"]
            end = sent["end"]
            sub_texts = _chunk_sentence(text, lang, max_chars)
            if len(sub_texts) == 1:
                blocks.append({"start": start, "end": end, "text": sub_texts[0]})
            else:
                total_chars = sum(len(s) for s in sub_texts)
                t = start
                for s in sub_texts:
                    portion = len(s) / total_chars
                    seg_dur = (end - start) * portion
                    blocks.append({"start": t, "end": t + seg_dur, "text": s})
                    t += seg_dur

    # 무음 구간 채우기: 블록 end를 다음 블록 start까지 연장
    for i in range(len(blocks) - 1):
        if blocks[i + 1]["start"] - blocks[i]["end"] > 0.05:
            blocks[i]["end"] = blocks[i + 1]["start"]

    return blocks


def _fmt_ass_time(seconds: float) -> str:
    """ASS 시간 형식: H:MM:SS.cc (centiseconds)"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    return text.replace("\n", "\\N").replace("{", "\\{").replace("}", "\\}")


def generate_ass_dual_line(segments: list[dict], ass_path: str, lang: str = "ko") -> int:
    """TTS 문장 타임스탬프 → ASS (2줄: 위=현재 흰색 / 아래=다음 회색 미리보기)."""
    blocks = _build_blocks(segments, lang)

    font = "Noto Sans CJK KR" if lang == "ko" else "DejaVuSans"
    cur_size = 52 if lang == "ko" else 48
    nxt_size = 44 if lang == "ko" else 40

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "WrapStyle: 2\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Current,{font},{cur_size},&H00FFFFFF,&H00000000,&H80000000,"
        f"1,0,0,0,100,100,0,0,1,3,1,2,40,40,140,1\n"
        f"Style: Next,{font},{nxt_size},&H00A0A0A0,&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,2,1,2,40,40,70,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = [header]
    for i, b in enumerate(blocks):
        start = _fmt_ass_time(b["start"])
        end = _fmt_ass_time(b["end"])
        cur_text = _ass_escape(b["text"])
        nxt_text = _ass_escape(blocks[i + 1]["text"]) if i + 1 < len(blocks) else ""

        lines.append(f"Dialogue: 0,{start},{end},Current,,0,0,0,,{cur_text}")
        if nxt_text:
            lines.append(f"Dialogue: 0,{start},{end},Next,,0,0,0,,{nxt_text}")

    Path(ass_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"  → {ass_path} ({len(blocks)}개 자막 블록, dual-line ASS)")
    return len(blocks)


def generate_srt_from_sentences(segments: list[dict], srt_path: str, lang: str = "ko") -> int:
    """scenes.json의 sentences 필드 → SRT (하위 호환 보존)."""
    blocks = _build_blocks(segments, lang)

    lines = []
    for i, b in enumerate(blocks, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_time(b['start'])} --> {_fmt_time(b['end'])}")
        lines.append(b["text"])
        lines.append("")

    Path(srt_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"  → {srt_path} ({len(blocks)}개 자막 블록)")
    return len(blocks)


# ── 하위 호환 (Whisper 폴백, 사용 안 함) ────────────────────────────────────────
def generate_srt(wav_path: str, srt_path: str, lang: str = "ko") -> int:
    raise NotImplementedError("Whisper 기반 자막 생성은 더 이상 사용하지 않습니다. generate_srt_from_sentences()를 호출하세요.")
