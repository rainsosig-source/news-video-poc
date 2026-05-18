"""대본 마크다운 → 발화 리스트 파싱."""
import re
from pathlib import Path


SKIP_PATTERNS = [
    r"^#",
    r"^\[",
    r"^\(",
    r"오프닝 멘트",
    r"클로징 멘트",
    r"본격적인 대화",
    r"본멘트",
]

SPEAKER_RE = re.compile(
    r"^[\*]*(상현|지민|진행자\s*[AB]|[AB]\s*[:\.]|Host\s*[AB])[\*]*\s*[:\.]?\s*",
    re.IGNORECASE,
)


def parse(script_path: str) -> list[dict]:
    """마크다운 대본 → [{idx, speaker, text}]"""
    text = Path(script_path).read_text(encoding="utf-8")
    lines = text.splitlines()

    results = []
    idx = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if any(re.search(p, line) for p in SKIP_PATTERNS):
            continue

        speaker = "unknown"
        content = line

        if re.search(r"^[\*]*(상현)", line, re.IGNORECASE):
            speaker = "상현"
            content = SPEAKER_RE.sub("", line)
        elif re.search(r"^[\*]*(지민)", line, re.IGNORECASE):
            speaker = "지민"
            content = SPEAKER_RE.sub("", line)

        content = content.replace("###", "").replace("**", "").strip()
        if not content:
            continue

        results.append({"idx": idx, "speaker": speaker, "text": content})
        idx += 1

    return results


if __name__ == "__main__":
    import sys, json
    path = sys.argv[1] if len(sys.argv) > 1 else "work/script_dialogue.md"
    lines = parse(path)
    for item in lines:
        print(f"[{item['idx']:02d}] {item['speaker']}: {item['text'][:50]}")
    print(f"\n총 {len(lines)}개 발화")
