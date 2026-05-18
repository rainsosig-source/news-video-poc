"""단락 단위 Flux 이미지 프롬프트 생성 (Claude CLI sonnet).

단락마다 고유 이미지 1장. 버킷/공유 없음 (품질 우선).
"""
import hashlib
import json
import re
import subprocess
import sys as _sys_for_vllm
if '/home/sddari/scripts' not in _sys_for_vllm.path:
    _sys_for_vllm.path.insert(0, '/home/sddari/scripts')
import vllm_client
from pathlib import Path


SYSTEM = """You are an art director designing a Korean news analysis video. For each narration paragraph, write ONE Flux image generation prompt that visually conveys the paragraph's core message.

You will receive (a) the original news article body for SPECIFIC context (entities, places, numbers, sectors), and (b) the narration paragraphs that will become images. Use the article to ground each image in its real-world subject — not generic shapes.

CORE PRINCIPLES:
- Identify the SPECIFIC subject of each paragraph using the article context (industry, place, action, actor)
- Choose CONCRETE visual elements that match the actual story (not generic "abstract shapes")
- Make each image VISUALLY DISTINCT from the others (different scenes, color palettes, compositions)
- Avoid repeating the same elements across prompts (no four "rising charts" in a row)

VISUAL FLOW STRUCTURE (paragraph position matters — match composition to narrative arc):
- Paragraphs 1-2 (intro / hook): WIDE establishing shot — a scene that sets the topic (skyline, marketplace, factory exterior, plaza). Wide angle, atmospheric.
- Paragraphs 3-N-2 (analysis / facts): MEDIUM or CLOSE shots — focused on a single concrete element (a hand, a tool, a doorway, a chart silhouette, a flow of objects). Tighter framing, higher contrast.
- Paragraphs N-1, N (closing / outlook): WIDE again — horizon, dawn/dusk, an open path, a quiet aftermath. Suggest forward motion or pause.
- Vary lighting across the arc: cool/neutral early → bold/saturated middle → warm/soft end. Helps emotional pacing.

CONCRETE VISUAL VOCABULARY (use specific scenes when relevant):
- Semiconductors/tech: a clean room with operator silhouette in bunny suit, a wafer reflecting light, a chip exposed on a circuit board, a fab building exterior at dawn
- Gas/fuel: gas station forecourts at dusk, fuel pump nozzles, refinery skylines, oil tankers at sea, pipelines through landscapes
- Government/policy: a podium with empty press microphones, a stylized capitol-like building, government documents on a desk, a balance scale
- Economy/finance: stacked coins, a cracked piggy bank, currency notes drifting, an empty trader's desk, abstract candlestick silhouettes
- Geopolitics: a stylized world map with pinpoints, shipping containers stacked at a port, a desert oil field at sunrise
- Supply chain: cargo trucks on highways, freight containers, factory smoke stacks at twilight
- Real estate/housing: an apartment block at twilight, an empty staircase, a key on a wooden table, a moving truck at a doorway
- Labor/delivery: a bicycle courier silhouette at night, packages in a sorting center, a clock on a warehouse wall
- Time/delay: an hourglass, ripples spreading on water, calendar pages turning
- Citizens/impact: anonymous silhouettes (no faces) at a relevant location, a parked car, a grocery basket with rising receipts

ART STYLE (always append exactly): "editorial illustration, flat vector, minimalist, modern news graphic style, soft color palette, no text, no logos, completely textless surfaces"

(IMPORTANT: do NOT use the word "Korean", "Hangul", "Asian", "Japanese", "Chinese" in the prompt — these words make Flux attempt to render Asian script which always comes out as gibberish glyphs. If the scene needs to feel Korean, describe NON-TEXTUAL Korean visual cues only: traditional roof tiles, mountains in background, hanok wall, Han River bridge silhouette — never mention the country name or culture name in the prompt.)

ABSOLUTE RULES — NO EXCEPTIONS:
- NEVER include any text, letters, numbers, words, characters, or glyphs in the image
- NEVER prompt for objects that inherently contain text: newspapers, books with visible pages, magazines, billboards, store signs, license plates, posters, screens with UI, smartphones with apps, computer monitors with content, ads, menus, receipts with numbers
- If text-bearing object is unavoidable, describe it as turned away, blurred, blank, or covered (e.g., "a newspaper folded with the front page hidden", "a closed book", "a screen turned off and dark")
- NO billboards, NO product labels, NO price tags, NO charts with axis labels or tick marks
- NO Korean characters, NO Latin letters, NO Arabic numerals like "$181" or "3.5%", NO Asian script of any kind
- If the concept involves a price or percentage, represent it ABSTRACTLY:
  * Price drop → coins falling, a downward arrow shape made of shadows, a deflating balloon
  * Percentage → a silhouette bar chart with NO labels, a pie slice shape
  * Brand name → a generic silhouette/icon shape only, no logo lettering
- All scenes must be PURELY VISUAL — no readable content anywhere in the frame
- All surfaces (walls, screens, papers, packaging) MUST be described as blank, smooth, or covered
- NO Ghibli/Pixar/Disney/anime style
- NO real people, NO celebrities, NO recognizable politicians
- NO brand logos, NO real corporate buildings (e.g., do NOT name "Samsung headquarters" — use "a generic glass-and-steel office tower")
- Anonymous silhouettes only — no facial detail
- Use VARIED color palettes across the images

OUTPUT FORMAT:
- Output ONLY the final prompt sentence, in English
- Length: 30-70 words (one or two sentences)
- Start directly with the visual subject (e.g., "A gas station forecourt at dusk...")
- Append at the end: ", no text, no letters, no signage, no labels, no typography, photorealistic"
- Do NOT include explanations, labels, paragraph numbers, or markdown
- Do NOT prepend "Prompt:" or quotes"""


_NO_TEXT_SUFFIX = ", no text, no letters, no signage, no labels, no typography, no glyphs, no Hangul, no Asian script, no writing of any kind, blank surfaces only, photorealistic"


def _claude(user_content: str) -> str:
    try:
        _out = vllm_client.chat(system=SYSTEM, user=user_content, timeout=120)
    except Exception as e:
        raise RuntimeError(f"vLLM 호출 오류: {e}") from e
    class _R:
        stdout = _out
        returncode = 0
    result = _R()
    text = result.stdout.strip()
    text = re.sub(r'^["\']|["\']$', '', text)
    text = re.sub(r'^(Prompt|prompt|이미지 프롬프트):\s*', '', text)
    text = text.strip()
    # 이미 suffix 포함되어 있으면 중복 방지
    if "no text" not in text:
        text += _NO_TEXT_SUFFIX
    return text


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:8]


def generate(ko_narration_path: str, prompts_json: str, article_text: str = "") -> list[dict]:
    """한국어 narration 단락 → 시각적으로 차별화된 이미지 프롬프트 리스트.
    article_text: 원본 기사 본문 (선택). 있으면 컨텍스트로 전달해 고유명사·수치·장소를 살림."""
    text = Path(ko_narration_path).read_text(encoding="utf-8")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    n = len(paragraphs)
    user_msg = ""
    if article_text:
        # 기사 본문이 너무 길면 앞 1500자만 (Flux 이미지 컨텍스트로 충분)
        excerpt = article_text.strip()[:1500]
        user_msg += f"=== 원본 기사 (시각 요소 단서로 사용) ===\n{excerpt}\n\n"
    user_msg += (
        f"=== 영상 단락 {n}개 ===\n"
        "각 단락에 대해 시각적으로 차별화된 이미지 프롬프트를 작성하세요. "
        "단락 위치(처음/중간/끝)에 따라 시각 흐름(wide-medium-wide)을 적용하세요. "
        "출력 형식은 단락 번호와 프롬프트만:\n\n"
    )
    for i, para in enumerate(paragraphs):
        user_msg += f"[{i+1}]\n{para}\n\n"
    user_msg += "출력:\n" + "\n".join(f"[{i+1}] (프롬프트 {i+1})" for i in range(n))

    print(f"  [Claude sonnet 일괄 생성: {n}단락]")
    raw = _claude(user_msg)

    # [1] (prompt) 형식 파싱
    prompts_text = []
    for i in range(n):
        m = re.search(rf"\[{i+1}\]\s*(.+?)(?=\n\[\d+\]|\Z)", raw, re.DOTALL)
        if m:
            p = m.group(1).strip()
            p = re.sub(r'^["\']|["\']$', '', p).strip()
            prompts_text.append(p)
        else:
            prompts_text.append("")

    # 빈 프롬프트만 단일 호출로 재시도
    for i, p in enumerate(prompts_text):
        if not p:
            print(f"  [재시도 {i+1}/{n}] 단일 호출")
            prompts_text[i] = _claude(f"단락:\n{paragraphs[i]}")

    # 단락마다 고유 이미지 (버킷/공유 없음)
    results = []
    for i, para in enumerate(paragraphs):
        prompt = prompts_text[i]
        sha = _sha(prompt)
        results.append({
            "idx": i,
            "ko_para": para[:80],
            "prompt": prompt,
            "sha": sha,
            "image_file": f"{i:02d}_{sha}.png",
        })
        print(f"    [{i+1}/{n}] {prompt[:80]}...")

    Path(prompts_json).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  → {prompts_json} ({len(results)}개 프롬프트)")
    return results


if __name__ == "__main__":
    items = generate("work/script_narration_ko.md", "work/prompts.json")
    for it in items:
        print(f"\n[{it['idx']}] {it['prompt']}")
