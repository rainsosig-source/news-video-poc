# DESIGN v4 — 워터마크 + 이미지 한글 회피 + 2줄 자막 C안

> 이 문서를 읽고 Sonnet이 그대로 구현한다. 의문 발생시 사용자에게 확인 후 진행.
>
> 변경 범위: `lib/compose.py`, `lib/align.py`, `lib/prompt_gen.py`, `poc.py`, `run_auto.py`

---

## A. 워터마크 — 출처 동적화 + 한글 깨짐 수정

### A-1. 현재 문제
`lib/compose.py`:
```python
WATERMARK = {
    "ko": "출처: 한국석유공사 오피넷 | AI 생성 영상",   # PoC 시절 하드코딩
    "en": "Source: KNOC Opinet | AI-Generated Content",
}
```
- 자동화 시스템에서 출처가 매번 다른데 항상 "한국석유공사"로 표시
- `drawtext text='...':`의 인라인 escape 문제로 한글 깨짐

### A-2. 수정안

**1) `WATERMARK` 딕셔너리 제거**, `compose.build()`에 `source` 파라미터 추가:

```python
def build(
    segments: list[dict],
    prompts: list[dict],
    images_dir: str,
    audio_path: str,
    srt_path: str,           # → ass_path 로 변경 (C안 적용 후)
    final_out: str,
    lang: str,
    work_dir: str,
    title: str = "",
    source: str = "",        # 신규: "연합뉴스", "조선일보" 등
) -> None:
```

**2) `_add_audio_subtitle()`도 `source` 받음**:

```python
def _add_audio_subtitle(
    video_path, audio_path, ass_path, out_path, lang,
    source: str = "",
    work_dir: str = "",
    add_watermark: bool = True,
):
    ...
    if add_watermark:
        wm_text = _build_watermark(source, lang)
        wm_file = os.path.join(work_dir, "watermark.txt")
        Path(wm_file).write_text(wm_text, encoding="utf-8")  # textfile= 사용
        # drawtext에서 textfile=로 읽음 (escape 문제 회피)
        vf_parts.append(
            f"drawtext=textfile='{wm_file}'"
            f":fontfile=/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
            f":fontsize=18:fontcolor=white@0.9:x=30:y=30"
            f":enable='lt(t\\,5)':box=1:boxcolor=black@0.5:boxborderw=8"
        )
```

**3) `_build_watermark()` 헬퍼 신규**:

```python
def _build_watermark(source: str, lang: str) -> str:
    if lang == "ko":
        src = source if source else "출처 미상"
        return f"출처: {src} | AI 생성 영상"
    else:
        src = source if source else "Source unknown"
        return f"Source: {src} | AI-Generated Content"
```

**4) `poc.py` 수정**: `--source` 인자 추가하고 `compose.build(source=args.source, ...)` 전달.

```python
ap.add_argument("--source", default="", help="기사 출처 (워터마크용)")
...
compose.build(..., source=args.source)
```

**5) `run_auto.py` 수정**: poc.py 호출 시 `--source` 전달.

```python
poc_cmd = [
    sys.executable, "poc.py",
    "--script", str(main_work / "script_dialogue.md"),
    "--ko-out", ko_out,
    "--en-out", en_out,
    "--source", article.get("source", ""),
    "--no-cache",
]
```

### A-3. 검증
- `compose.py` 단독 호출로 한글 워터마크 정상 표시 확인
- `ffprobe`로 첫 5초 프레임에서 텍스트 확인 어려움 → 영상 재생으로 시각 확인
- `run_auto.py` 실행 후 `article["source"]`가 "연합뉴스"라면 워터마크에 "출처: 연합뉴스" 표시되는지

---

## B. 이미지 프롬프트 — 한글 회피

### B-1. 현재 문제
Flux.1-schnell은 비ASCII 문자(특히 한글)를 그릴 수 없음. 프롬프트에 텍스트가 들어가면 깨진 글자가 출력됨.

### B-2. 수정안 (`lib/prompt_gen.py`)

**SYSTEM 프롬프트에 다음 추가**:

```python
ABSOLUTE RULES (강제):
- NEVER include any text, letters, numbers, words, or signage in the image
- NO billboards, NO product labels, NO price tags, NO charts with axis labels
- NO Korean characters (한글), NO numbers like "$181" or "3.5%"
- If concept involves text (price/percentage/brand), represent it ABSTRACTLY:
  * Price → coins/scales/arrow
  * Percentage → bar chart silhouette without labels
  * Brand → silhouette/icon shape only
- All scenes must be PURELY VISUAL — no readable content anywhere
```

**프롬프트 끝에 negative-style hint 추가**:

각 프롬프트 마지막에:
```
", no text, no letters, no signage, no labels, no typography, photorealistic"
```

### B-3. Diffusers negative prompt 활용 검토
`image_gen.py`의 Flux 호출:
```python
pipe(prompt=p, num_inference_steps=4, ...)
```
Flux.1-schnell은 negative_prompt 미지원이지만, prompt 안에 "no text" 같은 가드 문구를 넣으면 회피 효과 있음 (실험으로 검증됨).

### B-4. 검증
- 신규 영상 1편 생성 후 4장 이미지에서 한글/잘못된 글자 없는지 시각 확인
- 있으면 SYSTEM 프롬프트 추가 강화

---

## C. 자막 — C안 (항상 2줄, 위=현재 / 아래=다음 미리보기)

### C-1. 레이아웃

```
1920x1080 영상

         ... 영상 내용 ...

         (Y=85px from bottom, large white)
         현재 발화 자막
         (Y=50px from bottom, small gray)
         다음 발화 자막
```

- **현재 줄**: 흰색, 22px (KO) / 20px (EN), MarginV=85
- **다음 줄**: 회색 (#A0A0A0), 18px (KO) / 16px (EN), MarginV=50
- 마지막 자막일 때는 다음 줄 비움 (혹은 출처 한 줄)

### C-2. 포맷: SRT → ASS 전환

ffmpeg `subtitles` 필터는 SRT/ASS 둘 다 지원. ASS는 스타일/위치/색상 세밀 제어 가능.

**ASS 파일 구조**:
```
[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Current,NanumGothic,22,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,30,30,85,1
Style: Next,NanumGothic,18,&H00A0A0A0,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,30,30,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:03.00,Current,,0,0,0,,첫 번째 자막
Dialogue: 0,0:00:00.00,0:00:03.00,Next,,0,0,0,,두 번째 자막
Dialogue: 0,0:00:03.00,0:00:06.00,Current,,0,0,0,,두 번째 자막
Dialogue: 0,0:00:03.00,0:00:06.00,Next,,0,0,0,,세 번째 자막
```

**시간 형식**: `H:MM:SS.cc` (centi-seconds, 2자리)
**색상**: ASS는 BGR 순서 + 알파 (`&HAABBGGRR`)
- 흰색: `&H00FFFFFF`
- 회색: `&H00A0A0A0`
- 검정 외곽선: `&H00000000`

### C-3. `lib/align.py` 신규 함수

```python
def generate_ass_dual_line(
    segments: list[dict],
    ass_path: str,
    lang: str = "ko",
) -> int:
    """TTS 문장 타임스탬프 → ASS (2줄: 현재 + 다음 미리보기).

    각 문장 블록 i에 대해:
      - Current 스타일 이벤트 (현재 자막 텍스트)
      - Next 스타일 이벤트 (i+1 자막 텍스트, 마지막은 빈 문자열)
    두 이벤트는 동일한 시간 범위.
    """
    # 1) 기존 generate_srt_from_sentences와 동일한 chunk 로직으로 blocks 생성
    blocks = _build_blocks(segments, lang)  # [{start, end, text}, ...]

    # 2) ASS 헤더 작성
    font = "NanumGothic" if lang == "ko" else "DejaVuSans"
    cur_size = 22 if lang == "ko" else 20
    nxt_size = 18 if lang == "ko" else 16
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Current,{font},{cur_size},&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,30,30,85,1
Style: Next,{font},{nxt_size},&H00A0A0A0,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,30,30,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # 3) Dialogue 라인 작성
    lines = [header]
    for i, b in enumerate(blocks):
        start = _fmt_ass_time(b["start"])
        end = _fmt_ass_time(b["end"])
        cur_text = _ass_escape(b["text"])
        nxt_text = _ass_escape(blocks[i+1]["text"]) if i+1 < len(blocks) else ""

        lines.append(f"Dialogue: 0,{start},{end},Current,,0,0,0,,{cur_text}")
        if nxt_text:
            lines.append(f"Dialogue: 0,{start},{end},Next,,0,0,0,,{nxt_text}")

    Path(ass_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"  → {ass_path} ({len(blocks)}개 자막 블록, dual-line)")
    return len(blocks)


def _fmt_ass_time(seconds: float) -> str:
    """ASS 시간 형식: H:MM:SS.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    """ASS 텍스트 escape (쉼표, 줄바꿈 등)."""
    return text.replace("\n", "\\N").replace("{", "\\{").replace("}", "\\}")


def _build_blocks(segments: list[dict], lang: str) -> list[dict]:
    """기존 generate_srt_from_sentences 로직에서 SRT 출력 부분만 빼고
    blocks 리스트만 반환하도록 분리."""
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
    return blocks
```

**기존 `generate_srt_from_sentences`는 보존** (하위 호환). 신규 함수는 별도.

### C-4. `lib/compose.py` 수정

`_shift_srt` → `_shift_ass`로도 추가 (또는 분기):

```python
def _shift_ass(ass_path: str, shift_sec: float, out_path: str) -> None:
    """ASS Dialogue 라인의 Start/End 시간을 shift."""
    text = Path(ass_path).read_text(encoding="utf-8")
    out_lines = []
    for line in text.splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)  # Layer, Start, End, Style, Name, ML, MR, MV, Effect, Text
            parts[1] = _shift_ass_time(parts[1], shift_sec)
            parts[2] = _shift_ass_time(parts[2], shift_sec)
            out_lines.append(",".join(parts))
        else:
            out_lines.append(line)
    Path(out_path).write_text("\n".join(out_lines), encoding="utf-8")


def _shift_ass_time(t_str: str, shift: float) -> str:
    h, m, s = t_str.split(":")
    total = int(h)*3600 + int(m)*60 + float(s) + shift
    nh = int(total // 3600)
    nm = int((total % 3600) // 60)
    ns = total % 60
    return f"{nh}:{nm:02d}:{ns:05.2f}"
```

`_add_audio_subtitle()`의 `subtitles` 필터는 동일 (ASS 자동 인식):

```python
vf_parts = [f"subtitles='{ass_path}'"]   # force_style 제거 (ASS 자체에 정의됨)
```

`build()` 시그니처:
```python
def build(
    segments, prompts, images_dir, audio_path,
    ass_path: str,           # srt_path → ass_path (이름 변경)
    final_out, lang, work_dir, title="", source="",
):
    ...
    if title:
        shifted_ass = os.path.join(work_dir, "subtitles_shifted.ass")
        _shift_ass(ass_path, TITLE_SEC, shifted_ass)
        final_subtitle = shifted_ass
    else:
        final_subtitle = ass_path
    ...
    _add_audio_subtitle(slideshow, final_audio, final_subtitle, final_out, lang,
                        source=source, work_dir=work_dir)
```

### C-5. `poc.py` 수정 (Phase 4 + Phase 7)

```python
# Phase 4
ko_ass = f"{WORK}/ko/subtitles.ass"   # .srt → .ass
en_ass = f"{WORK}/en/subtitles.ass"

if args.no_cache or not Path(ko_ass).exists():
    _, t = _timer("KO 자막", align.generate_ass_dual_line, ko_segments, ko_ass, "ko")
...

# Phase 7
compose.build(
    ...,
    ass_path=ko_ass,      # srt_path → ass_path
    source=args.source,
    ...
)
```

### C-6. 엣지 케이스
1. **마지막 자막**: 다음 줄 비움 (Next 이벤트 생략)
2. **자막 블록 < 2개**: Current만 표시, Next 영역 빈공간
3. **다음 자막이 너무 빨리 와서 이전 Next와 겹치는 경우**: ASS는 동시 발생 자막을 layer 순서로 처리하므로 문제 없음. 단, 가독성 위해 같은 시간대 Current/Next는 한 쌍으로만.
4. **Bark TTS 사이 무음 구간**: 무음 구간 동안에도 다음 줄 미리보기가 떠 있어야 자연스러움 → 현재 자막의 end 시간을 다음 자막의 start까지 연장하는 옵션 검토 (아래 5-A 참조)

### C-7. (선택적 개선) 무음 구간 자막 채우기

자막 블록 사이 공백이 0.3초 이상이면 시청자에게 자막 사라지는 것이 어색할 수 있음. 두 옵션:

- **옵션 1**: 각 블록의 `end`를 다음 블록 `start`까지 연장 (단순)
- **옵션 2**: 마지막 블록 빼고 모든 블록의 end를 다음 블록 start로 (현재 구현이 거의 이렇지만 약간의 갭 존재)

C안 적용 시 **옵션 1로 처리** 권장. `_build_blocks` 후에 후처리:

```python
for i in range(len(blocks) - 1):
    if blocks[i+1]["start"] - blocks[i]["end"] > 0.05:
        blocks[i]["end"] = blocks[i+1]["start"]
```

---

## D. 구현 순서 (Sonnet 작업 순서)

### Step 1: 워터마크 (A)
1. `lib/compose.py`:
   - `WATERMARK` 딕셔너리 제거
   - `_build_watermark(source, lang)` 추가
   - `_add_audio_subtitle()`에 `source`, `work_dir` 파라미터 추가, `textfile=` 사용
   - `build()`에 `source` 파라미터 추가
2. `poc.py`: `--source` 인자, `compose.build(source=args.source)`
3. `run_auto.py`: `--source` 전달
4. **검증**: 기존 work/ 캐시 그대로 두고 `compose.py` 단독 호출 또는 `poc.py --source "연합뉴스"`로 영상 1개 재생성, 워터마크 확인

### Step 2: 이미지 한글 회피 (B)
1. `lib/prompt_gen.py`:
   - SYSTEM에 ABSOLUTE RULES 추가
   - 각 프롬프트 결과에 ", no text, no letters, no signage" 자동 append
2. **검증**: `work/prompts.json` 삭제 후 `poc.py --no-cache`로 재생성, 이미지 4장 시각 확인

### Step 3: 2줄 자막 C안 (C)
1. `lib/align.py`: `generate_ass_dual_line()`, `_fmt_ass_time()`, `_ass_escape()`, `_build_blocks()` 추가
2. `lib/compose.py`: `_shift_ass()` 추가, `build()` 시그니처 `srt_path`→`ass_path` 변경, `_add_audio_subtitle()`의 `subtitles` 필터 force_style 제거
3. `poc.py`: Phase 4 자막 경로 `.srt`→`.ass`, Phase 7 인자 변경
4. **검증**: `work/ko/subtitles.ass` 직접 열어 ASS 형식 확인 + 영상 재생으로 2줄 표시 확인

### Step 4: 통합 테스트
- `work/` 전체 삭제
- `python run_auto.py` 실행 (또는 기존 영상 1편 재생성)
- 결과 영상 3가지 모두 확인

---

## E. 호환성 / 폴백

- 기존 `generate_srt_from_sentences()` 함수는 **삭제 X, 보존**. 다른 곳에서 호출 안하므로 dead code지만 향후 비교용.
- `compose.build()`의 파라미터명 `srt_path`→`ass_path` 변경은 **breaking**. 호출처는 `poc.py` 한 곳뿐이므로 같이 수정.
- ASS는 ffmpeg 6.x에서 안정 지원. 시스템 ffmpeg 6.1.1 확인됨.

---

## F. 위험요소

1. **ASS 폰트 미적용**: ffmpeg subtitles 필터는 fontconfig를 통해 폰트를 찾음. NanumGothic 시스템에 설치되어 있는지 확인:
   ```
   fc-list | grep -i nanum
   ```
   없으면 `/usr/share/fonts/truetype/nanum/NanumGothic.ttf` 경로의 폰트가 fontconfig에 등록 안됐을 가능성. `fc-cache -f -v` 실행 또는 ASS Style의 `Fontname`을 정확한 family name으로.

2. **두 줄 자막이 영상 콘텐츠 가림**: MarginV 값 조정으로 영상 하단 여백 확보. 1080p에서 50~85px 정도면 통상 문제 없음. 영상 보고 조정.

3. **Next 줄이 시각적으로 헷갈림**: 회색 + 작은 폰트로 충분히 구분되는지 시각 확인. 안 되면 색상을 더 어둡게(`&H00606060`) 또는 알파 추가(`&H40A0A0A0`).

---

## G. 완료 기준

- [ ] 워터마크에 실제 출처(예: "연합뉴스") 표시되고 한글 정상
- [ ] 이미지 4장 모두 한글/숫자/문자 글리치 없음
- [ ] 영상 재생 시 항상 2줄 자막 표시 (위=현재 흰색 큰 글자, 아래=다음 회색 작은 글자)
- [ ] 마지막 자막은 단일 줄
- [ ] 타이틀 슬라이드 3초 + 자막 시간 shift 정상 작동
