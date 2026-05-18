"""어려운 경제 용어를 감지하고 중학생 수준 해설 박스를 생성한다.

플로우:
  1. narration 텍스트 → Claude로 어려운 경제·금융·정책 용어 추출 + 해설 작성
  2. segments(scenes.json)의 sentence 타임스탬프와 매칭하여 박스 표시 시각 결정
  3. 박스 간 시간 겹침 방지 (후속 박스 start를 이전 box end 이후로 밀기)
  4. ASS 자막 파일로 출력 — top-right corner, 페이드 인/아웃, 무음 (시각만)

사용:
  glossary = glossary_gen.generate(narration_path, segments, lang, out_json)
  glossary_gen.to_ass(glossary, ass_path, lang)
"""
import json
import subprocess
import sys as _sys_for_vllm
if '/home/sddari/scripts' not in _sys_for_vllm.path:
    _sys_for_vllm.path.insert(0, '/home/sddari/scripts')
import vllm_client
from pathlib import Path

MAX_TERMS = 4              # 영상당 박스 최대 개수
BOX_HOLD_SEC = 5.0         # 박스 표시 최소 지속 시간
BOX_GAP_SEC = 0.5          # 박스 간 최소 간격

SYSTEM_KO = """너는 뉴스 영상 자막 편집자다. 주어진 한국어 뉴스 분석 대본에서 일반 시청자(중학생 수준)가 모를 가능성이 높은 경제·금융·정책 전문 용어를 식별하고, 각 용어에 대해 1줄 해설을 작성하라.

선택 기준:
- 일반인이 모를 가능성이 높은 전문 용어 (예: 기준금리, 양적완화, 유동성, 스태그플레이션, 환율방어, 채권수익률, 기축통화)
- 영상에 처음 나오는 시점의 용어만 (반복 등장은 첫 번째에서만)
- 동일 영상에서 최대 4개까지만 (너무 많으면 시청 방해)
- 너무 일상적인 단어 제외 (예: 가격, 회사, 정부, 시장, 경기)
- 대본에 실제로 등장한 단어와 정확히 일치하는 형태로 추출 (조사 제거: "기준금리는" → "기준금리")

해설 작성 원칙:
- 중학생도 이해할 수 있는 쉬운 한국어 (한자어 최소화)
- 30자 이내, 명사형으로 끝맺음
- 정확한 정의 우선, 비유는 필요할 때만
- 예: "기준금리" → "한국은행이 정하는 가장 중요한 금리"
- 예: "양적완화" → "중앙은행이 돈을 풀어 채권을 사는 정책"

출력 형식 (JSON 배열만, 다른 텍스트·마크다운·설명 금지):
[
  {"term": "기준금리", "definition": "한국은행이 정하는 가장 중요한 금리"},
  {"term": "양적완화", "definition": "중앙은행이 돈을 풀어 채권을 사는 정책"}
]

용어가 없으면 빈 배열 []만 출력."""

SYSTEM_EN = """You are a news video editor. From the given English narration script, identify difficult economic/financial/policy terms that a general viewer (middle-school level) might not understand, and write a one-line explanation for each.

Selection criteria:
- Terms general viewers likely don't know (e.g., quantitative easing, stagflation, yield curve, reserve currency, basis points, repo rate)
- Only first appearance of each term (skip if it repeats)
- Maximum 4 terms per video
- Exclude everyday words (price, company, government, market, economy)
- Extract the exact form as it appears in the script

Definition rules:
- Plain English a middle schooler can understand
- Under 60 characters
- Accuracy first, analogies only when needed
- Example: "quantitative easing" → "When central banks create money to buy bonds"

Output format (JSON array only, no markdown, no other text):
[
  {"term": "quantitative easing", "definition": "When central banks create money to buy bonds"}
]

If no qualifying terms, output an empty array []."""


def _claude(narration: str, lang: str) -> list[dict]:
    system = SYSTEM_KO if lang == "ko" else SYSTEM_EN
    try:
        _out = vllm_client.chat(system=system, user=narration, timeout=120)
    except Exception as e:
        raise RuntimeError(f"vLLM 호출 오류: {e}") from e
    class _R:
        stdout = _out
        returncode = 0
    result = _R()

    raw = result.stdout.strip()
    if "```" in raw:
        # ```json ... ``` 또는 ``` ... ``` 처리
        parts = raw.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("["):
                raw = p
                break
        else:
            raw = parts[1] if len(parts) > 1 else raw

    raw = raw.strip()

    try:
        terms = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [{lang}] glossary JSON 파싱 실패: {e}; raw={raw[:200]}")
        return []

    if not isinstance(terms, list):
        return []

    out = []
    for t in terms[:MAX_TERMS]:
        if isinstance(t, dict) and "term" in t and "definition" in t:
            term = str(t["term"]).strip()
            defn = str(t["definition"]).strip()
            if term and defn:
                out.append({"term": term, "definition": defn})
    return out


def _find_term_timing(term: str, segments: list[dict]) -> tuple[float, float] | None:
    """segments에서 term이 처음 등장하는 sentence의 (start, end). 없으면 None."""
    for seg in segments:
        for sent in seg.get("sentences", []):
            if term in sent["text"]:
                return sent["start"], sent["end"]
    return None


def generate(narration_path: str, segments: list[dict], lang: str, out_json: str) -> list[dict]:
    """narration → 용어 추출 + 타이밍 매칭. 결과를 out_json에 저장하고 리스트 반환."""
    narration = Path(narration_path).read_text(encoding="utf-8")
    terms = _claude(narration, lang)
    print(f"  [{lang}] Claude 추출 용어: {len(terms)}개")

    enriched = []
    for t in terms:
        timing = _find_term_timing(t["term"], segments)
        if timing is None:
            print(f"    ! 타이밍 매칭 실패 (스크립트에 없음): {t['term']}")
            continue
        start, sent_end = timing
        # 박스 표시 종료: sentence_end + 2초 또는 start + 5초 중 큰 값
        box_end = max(sent_end + 2.0, start + BOX_HOLD_SEC)
        enriched.append({
            "term": t["term"],
            "definition": t["definition"],
            "start": start,
            "end": box_end,
        })

    # 시간 정렬 후 겹침 방지: 후속 박스를 이전 박스 종료 + GAP 만큼 뒤로 밀기
    enriched.sort(key=lambda x: x["start"])
    for i in range(1, len(enriched)):
        min_start = enriched[i - 1]["end"] + BOX_GAP_SEC
        if enriched[i]["start"] < min_start:
            shift = min_start - enriched[i]["start"]
            enriched[i]["start"] += shift
            enriched[i]["end"] += shift

    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(out_json).write_text(
        json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  → {out_json} ({len(enriched)}개 용어 박스)")
    return enriched


def _fmt_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    """ASS 텍스트 이스케이프 (override 블록 제외)."""
    return text.replace("\\", "\\\\").replace("\n", " ").replace("{", "\\{").replace("}", "\\}")


def to_ass(glossary: list[dict], ass_path: str, lang: str) -> None:
    """glossary 리스트를 ASS 자막으로 저장. 빈 리스트여도 유효한 ASS 헤더 생성."""
    font = "Noto Sans CJK KR" if lang == "ko" else "DejaVuSans"

    # BorderStyle=3: 텍스트 뒤에 불투명 박스 (Outline 값이 패딩처럼 작동)
    # BackColour &HD0000000: 검은색, 약 80% 불투명
    # Alignment=9: top-right (시계 방향 keypad: 7=top-left, 8=top-center, 9=top-right)
    # MarginR=40, MarginV=40: 화면 우상단 여백
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Glossary,{font},30,&H00FFFFFF,&H00000000,&HD0202020,"
        f"0,0,0,0,100,100,0,0,3,20,0,9,50,50,50,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = [header]
    # 페이드 400ms in/out, term은 굵은 노란색 32pt, 정의는 흰색 24pt
    # ASS BGR 색상: #FFD700 (gold) → BGR &H00D7FF
    for g in glossary:
        start = _fmt_ass_time(g["start"])
        end = _fmt_ass_time(g["end"])
        term = _ass_escape(g["term"])
        defn = _ass_escape(g["definition"])
        text = (
            f"{{\\fad(400,400)}}"
            f"{{\\b1\\fs46\\c&H00D7FF&}}"
            f"{term}"
            f"{{\\r\\fs34}}"
            f"\\N{defn}"
        )
        # Layer=1 (자막보다 위에)
        lines.append(f"Dialogue: 1,{start},{end},Glossary,,50,50,50,,{text}")

    Path(ass_path).parent.mkdir(parents=True, exist_ok=True)
    Path(ass_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"  → {ass_path}")


def has_events(ass_path: str) -> bool:
    """ASS 파일에 Dialogue 라인이 있는지. compose에서 vf 추가 여부 판단용."""
    if not Path(ass_path).exists():
        return False
    text = Path(ass_path).read_text(encoding="utf-8")
    return any(line.startswith("Dialogue:") for line in text.splitlines())


if __name__ == "__main__":
    import sys
    lang = sys.argv[1] if len(sys.argv) > 1 else "ko"
    narration = f"work/script_narration_{lang}.md"
    scenes = f"work/{lang}/scenes.json"
    out_json = f"work/{lang}/glossary.json"
    out_ass = f"work/{lang}/glossary.ass"
    segments = json.loads(Path(scenes).read_text())
    g = generate(narration, segments, lang, out_json)
    to_ass(g, out_ass, lang)
    print(json.dumps(g, ensure_ascii=False, indent=2))
