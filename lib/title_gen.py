"""영상 제목 생성 (Claude CLI sonnet)."""
import subprocess
import sys as _sys_for_vllm
if '/home/sddari/scripts' not in _sys_for_vllm.path:
    _sys_for_vllm.path.insert(0, '/home/sddari/scripts')
import vllm_client
from pathlib import Path


KO_SYSTEM = """너는 뉴스 영상 편집자다. 주어진 한국어 분석 내레이션을 보고 영상 첫 화면에 띄울 제목을 만들어라.

규칙:
- 정확히 한 줄, 12~25자 한국어
- 뉴스 헤드라인 톤 (자극적이거나 추상적이지 말 것)
- 핵심 사실 + 간결한 시점
- 출력은 제목 텍스트만 (따옴표, 접두사, 마크다운 없이)"""

EN_SYSTEM = """You are a news video editor. Read the English analysis narration and write a video title for the opening title card.

Rules:
- Exactly ONE line, 6-12 English words
- Headline tone (no clickbait, no abstract phrasing)
- Concrete facts and a clear angle
- Output the title text only (no quotes, prefixes, or markdown)"""


def generate(narration_path: str, lang: str) -> str:
    text = Path(narration_path).read_text(encoding="utf-8")
    system = KO_SYSTEM if lang == "ko" else EN_SYSTEM
    try:
        _out = vllm_client.chat(system=system, user=text, timeout=120)
    except Exception as e:
        raise RuntimeError(f"vLLM 호출 오류: {e}") from e
    class _R:
        stdout = _out
        returncode = 0
    result = _R()
    title = result.stdout.strip().strip('"').strip("'").strip()
    return title
