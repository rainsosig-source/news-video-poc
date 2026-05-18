"""dialogue 대본 → 한국어 분석 narration → 영어 현지화 (Claude CLI sonnet)."""
import re
import subprocess
import sys as _sys_for_vllm
if '/home/sddari/scripts' not in _sys_for_vllm.path:
    _sys_for_vllm.path.insert(0, '/home/sddari/scripts')
import vllm_client
from pathlib import Path


KO_SYSTEM = """너는 데이터 기반 뉴스 분석 진행자다. 주어진 대화 형식 대본을 단일 내레이터의 분석 영상 대본으로 재구성한다.

규칙:
- 출력은 한국어 평어체 내레이션, 8~12단락
- 구조 가이드 (단락당 핵심 메시지 1개씩 분리):
  * 인트로 (1단락): 핵심 이슈를 한 줄로 도입
  * 사실 정리 (2~3단락): 구체적 사실·숫자·배경을 단락마다 1개씩
  * 분석 (3~5단락): 비교·영향·fact-check·맥락을 단락마다 1개씩 분리
  * 마무리 (1~2단락): 시사점·전망
- 각 단락은 70~130자, 1~2문장 — 한 화면(이미지) 분량
- 분석 섹션에 비교/영향/fact-check/맥락 중 3개 이상 포함
- 원문 표현을 그대로 인용하지 말 것 (직접 인용 금지)
- 사실/숫자만 추출해서 본인의 말로 재구성
- 분석/논평/맥락 추가가 본문 중 60% 이상 차지해야 함
- 출처가 불명확한 수치는 "보도된 바에 따르면" 같은 단서 사용
- 화자 표시 없음
- 단락 구분: 빈 줄 하나만 사용 (다른 마크다운/번호/기호 없음)
- 영상 길이 2~3분 목표 (원고 글자수 약 800~1200자)"""

EN_SYSTEM = """You are a professional translator who presents Korean news to English-speaking audiences.
Translate the given Korean analysis narration into natural English.

Rules:
- Korean-specific context (won-dollar rate, ministry names, etc.): briefly explain on first mention
  Example: "the Ministry of Trade, Industry and Energy (MOTIE)"
  Example: "Korean won (approximately 0.00073 USD per won)"
- Tone: analytical, calm, newsroom style (BBC/NPR tone)
- Length: within ±15% of Korean script length
- Keep EXACTLY the same number of paragraphs as the Korean input
- Paragraph separator: exactly ONE blank line between paragraphs — no markdown, no numbers, no symbols
- Output plain English text only, no metadata, no section labels"""


def _claude(user_content: str, system: str) -> str:
    try:
        _out = vllm_client.chat(system=system, user=user_content, timeout=300)
    except Exception as e:
        raise RuntimeError(f"vLLM 호출 오류: {e}") from e
    class _R:
        stdout = _out
        returncode = 0
    result = _R()
    return result.stdout.strip()


def _count_paragraphs(text: str) -> int:
    return len([p for p in text.split("\n\n") if p.strip()])


def dialogue_to_ko(dialogue_text: str) -> str:
    resp = _claude(f"대화:\n{dialogue_text}", KO_SYSTEM)
    # 마크다운 헤더/번호 제거
    resp = re.sub(r"^#{1,3}\s+.*$", "", resp, flags=re.MULTILINE)
    resp = re.sub(r"^\d+\.\s+", "", resp, flags=re.MULTILINE)
    resp = re.sub(r"\n{3,}", "\n\n", resp).strip()
    return resp


def ko_to_en(ko_text: str, max_retries: int = 3) -> str:
    for attempt in range(max_retries):
        resp = _claude(ko_text, EN_SYSTEM)
        resp = re.sub(r"\n{3,}", "\n\n", resp).strip()
        ko_paras = _count_paragraphs(ko_text)
        en_paras = _count_paragraphs(resp)
        if en_paras == ko_paras:
            return resp
        print(f"  단락 수 불일치 (KO={ko_paras}, EN={en_paras}), 재시도 {attempt+1}/{max_retries}")
    raise RuntimeError(f"영어 번역 단락 수 불일치 {max_retries}회 실패")


def run(dialogue_path: str, ko_out: str, en_out: str) -> tuple[str, str]:
    dialogue = Path(dialogue_path).read_text(encoding="utf-8")

    print("  [2-1] 한국어 분석 narration 생성 중 (Claude sonnet)...")
    ko = dialogue_to_ko(dialogue)
    Path(ko_out).write_text(ko, encoding="utf-8")
    print(f"  한국어 {_count_paragraphs(ko)}단락, {len(ko)}자 저장 → {ko_out}")

    print("  [2-2] 영어 현지화 중 (Claude sonnet)...")
    en = ko_to_en(ko)
    Path(en_out).write_text(en, encoding="utf-8")
    print(f"  영어 {_count_paragraphs(en)}단락 저장 → {en_out}")

    return ko, en


if __name__ == "__main__":
    ko, en = run(
        "work/script_dialogue.md",
        "work/script_narration_ko.md",
        "work/script_narration_en.md",
    )
    print("\n=== 한국어 대본 ===")
    print(ko)
    print("\n=== 영어 대본 ===")
    print(en)
