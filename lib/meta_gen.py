"""YouTube 업로드 메타 파일 생성 (Claude CLI haiku, 한/영 각각)."""
import re
import subprocess
import sys as _sys_for_vllm
if '/home/sddari/scripts' not in _sys_for_vllm.path:
    _sys_for_vllm.path.insert(0, '/home/sddari/scripts')
import vllm_client
from pathlib import Path


KO_SYSTEM = """너는 YouTube 콘텐츠 전략가다. 주어진 한국어 뉴스 분석 영상 대본을 보고 YouTube 업로드용 메타데이터를 생성한다.
출력 형식 (정확히 이 형식으로):
제목1: (제목 후보 1)
제목2: (제목 후보 2)
제목3: (제목 후보 3)
설명: (3~4줄 설명)
태그: 태그1,태그2,태그3,태그4,태그5"""

EN_SYSTEM = """You are a YouTube content strategist. Given an English news analysis script, generate YouTube upload metadata.
Output format (exactly this format):
Title1: (title candidate 1)
Title2: (title candidate 2)
Title3: (title candidate 3)
Description: (3-4 line description)
Tags: tag1,tag2,tag3,tag4,tag5"""

CHECKLIST_KO = """
[업로드 체크리스트]
[ ] "변형 또는 합성 콘텐츠" 체크 (필수)
[ ] 카테고리: 뉴스 및 정치
[ ] 어린이용 콘텐츠 아님
[ ] 자막 언어: 한국어
[ ] 영어판 영상 URL 설명에 추가 (영어판 업로드 후)
[ ] 출처 URL 모두 유효한지 확인
[ ] 사실 오류 없는지 본인 검토
[ ] 첫 5초 출처 워터마크 확인"""

CHECKLIST_EN = """
[Upload Checklist]
[ ] Tick "Altered or synthetic content" (required)
[ ] Category: News & Politics
[ ] Not for kids
[ ] Subtitle language: English
[ ] Add Korean video URL to description (after upload)
[ ] Verify all source URLs are valid
[ ] Fact-check before publish
[ ] Watermark visible in first 5 seconds"""

DISCLAIMER_KO = """⚠️ 본 영상은 AI 음성/이미지 합성을 사용했습니다.
   분석 내용은 공개 자료에 기반하며, 투자/소비 결정의 근거가 될 수 없습니다.

🌐 English version: <영어판 업로드 후 URL 입력>"""

DISCLAIMER_EN = """⚠️ This video uses AI-generated voice and imagery.
   Analysis is based on public data and should not be used for investment or consumer decisions.

🌐 한국어판: <Korean video URL after upload>"""


def _claude(user_content: str, system: str) -> str:
    try:
        return vllm_client.chat(system=system, user=user_content, timeout=120).strip()
    except Exception as e:
        raise RuntimeError(f"vLLM 호출 오류: {e}") from e


def _parse_meta(raw: str) -> dict:
    result = {}
    for line in raw.splitlines():
        for key in ("Title1", "Title2", "Title3", "제목1", "제목2", "제목3",
                    "Description", "설명", "Tags", "태그"):
            if line.startswith(f"{key}:"):
                result[key] = line.split(":", 1)[1].strip()
    return result


def generate_ko(narration_path: str, out_path: str) -> None:
    text = Path(narration_path).read_text(encoding="utf-8")
    raw = _claude(text, KO_SYSTEM)
    meta = _parse_meta(raw)

    titles = [meta.get(f"제목{i}", f"(제목 후보 {i})") for i in range(1, 4)]
    desc = meta.get("설명", "(설명 생략)")
    tags = meta.get("태그", "뉴스,분석,AI생성")

    content = f"""=========================================================
YouTube 업로드 메타 (한국어) — 사람 검토 필수
=========================================================

[제목 후보]
1) {titles[0]}
2) {titles[1]}
3) {titles[2]}

[설명란]
{desc}

📌 출처:
- <출처 URL 직접 입력>

{DISCLAIMER_KO}

#{' #'.join(t.strip() for t in tags.split(',')[:5])}

[태그]
{tags}
{CHECKLIST_KO}
"""
    Path(out_path).write_text(content, encoding="utf-8")
    print(f"  → {out_path}")


def generate_en(narration_path: str, out_path: str) -> None:
    text = Path(narration_path).read_text(encoding="utf-8")
    raw = _claude(text, EN_SYSTEM)
    meta = _parse_meta(raw)

    titles = [meta.get(f"Title{i}", f"(Title candidate {i})") for i in range(1, 4)]
    desc = meta.get("Description", "(description omitted)")
    tags = meta.get("Tags", "korea news,analysis,AI generated")

    content = f"""=========================================================
YouTube Upload Meta (English) — Human Review Required
=========================================================

[Title Candidates]
1) {titles[0]}
2) {titles[1]}
3) {titles[2]}

[Description]
{desc}

📌 Sources:
- <Add source URLs here>

{DISCLAIMER_EN}

#{' #'.join(t.strip() for t in tags.split(',')[:5])}

[Tags]
{tags}
{CHECKLIST_EN}
"""
    Path(out_path).write_text(content, encoding="utf-8")
    print(f"  → {out_path}")


if __name__ == "__main__":
    generate_ko("work/script_narration_ko.md", "/tmp/test_ko.youtube.txt")
    print(open("/tmp/test_ko.youtube.txt").read())
