"""기사 본문 → 상현/지민 대화 대본 생성 (Claude sonnet)."""
import subprocess
import sys as _sys_for_vllm
if '/home/sddari/scripts' not in _sys_for_vllm.path:
    _sys_for_vllm.path.insert(0, '/home/sddari/scripts')
import vllm_client
from pathlib import Path


SYSTEM = """너는 뉴스 분석 팟캐스트 작가다. 주어진 기사 본문을 바탕으로
상현(진행자)과 지민(분석가)의 대화 형식 대본을 써라.

규칙:
- 발화 형식: 정확히 "상현: 내용" 또는 "지민: 내용" (볼드/기타 마크다운 없이)
- 총 14~20줄 발화 (각 발화는 1~3문장)
- 구조: 인트로(2줄) → 사실정리(4~6줄) → 분석강화(6~8줄) → 마무리(2줄)
- 분석 강화 섹션에 반드시 포함: 수치 비교 또는 생활 영향 또는 맥락 설명
- 출처 불명확한 수치는 "보도에 따르면" 같은 단서 사용
- 연예/스포츠/광고 내용 절대 포함 금지
- 한국어만 사용
- 제목/헤더/목차 없이 대화만 출력"""


def generate(article: dict, article_text: str) -> str:
    """기사 → 대화 대본 마크다운. article={title, source, pub_date, ...}"""
    title = article.get("title", "")
    source = article.get("source", "")
    pub_date = article.get("pub_date", "")[:10]

    user_msg = f"""기사 제목: {title}
출처: {source}
날짜: {pub_date}

기사 본문:
{article_text[:6000]}"""

    try:
        _out = vllm_client.chat(system=SYSTEM, user=user_msg, timeout=300)
    except Exception as e:
        raise RuntimeError(f"vLLM 호출 오류: {e}") from e
    class _R:
        stdout = _out
        returncode = 0
    result = _R()

    dialogue = result.stdout.strip()
    # 메타 헤더 추가 (parser.py가 # 로 시작하는 줄 skip하므로 안전)
    header = f"# {title}\n# 출처: {source} ({pub_date})\n\n"
    return header + dialogue


if __name__ == "__main__":
    import json, sys
    test_article = {
        "title": "테스트 뉴스",
        "source": "연합뉴스",
        "pub_date": "2026-05-02",
        "link": "",
    }
    test_text = "이것은 테스트 기사 본문입니다. 경제 지표가 상승했습니다."
    print(generate(test_article, test_text))
