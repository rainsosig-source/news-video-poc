"""Claude sonnet으로 뉴스 후보 중 영상 제작할 1건 선택."""
import json
import subprocess
import sys as _sys_for_vllm
if '/home/sddari/scripts' not in _sys_for_vllm.path:
    _sys_for_vllm.path.insert(0, '/home/sddari/scripts')
import vllm_client
from pathlib import Path


SYSTEM = """너는 뉴스 영상 프로듀서다. 주어진 뉴스 후보 목록에서 오늘 영상으로 만들 기사 1건을 골라라.

선택 기준 (우선순위 순):
1. 경제·금융·산업·기술·정책 분야 (연예/스포츠/사건사고 완전 제외)
   - 국제외교·정치 기사도 한국 경제·산업·물가·무역·기업에 직접 영향을 미치는 경우 포함
     (예: 미국 관세 정책, 이란·중동 긴장에 따른 유가/에너지 영향, 외교 협상 결과로 인한 수출 변화 등)
2. 시청자가 실생활에서 체감할 수 있는 주제 (물가, 금리, 채용, 부동산 등)
3. 데이터/수치가 포함되어 분석이 가능한 기사
4. 오늘 처음 나온 뉴스 (이미 많이 다뤄진 주제 보다 신선한 것)
5. 화이트리스트 출처(연합/SBS/KBS/MBC/한겨레/조선/중앙/동아/매경/한경/이데일리) 우선

이미 다뤄진 기사(already_covered 목록 참조)와 유사한 주제는 피해라.

출력 형식 (JSON만, 다른 텍스트 없이):
{
  "selected_id": "기사 id",
  "reason": "선택 이유 1줄",
  "skip": false
}

만약 모든 후보가 부적합하거나(연예/스포츠/사건사고만 있거나) 이미 다 다뤄진 주제면:
{
  "selected_id": null,
  "reason": "스킵 이유",
  "skip": true
}"""


def select(articles: list[dict], already_covered: list[str] = None) -> dict:
    """articles 목록에서 최선 1건을 선택. 반환: {selected_id, reason, skip, article}"""
    if not articles:
        return {"selected_id": None, "reason": "후보 없음", "skip": True, "article": None}

    already_covered = already_covered or []

    # 후보가 너무 많으면 Claude 처리 시간 증가 → 80건으로 제한 (시간 역순 = 최신 우선)
    MAX_CANDIDATES = 80
    candidates = articles[:MAX_CANDIDATES] if len(articles) > MAX_CANDIDATES else articles

    candidates_text = json.dumps(
        [{"id": a["id"], "title": a["title"], "source": a["source"],
          "summary": a.get("summary", "")[:200]} for a in candidates],
        ensure_ascii=False, indent=2
    )

    user_msg = f"""뉴스 후보:
{candidates_text}

이미 다뤄진 주제 (오늘):
{json.dumps(already_covered, ensure_ascii=False)}"""

    try:
        _out = vllm_client.chat(system=SYSTEM, user=user_msg, timeout=240)
    except Exception as e:
        raise RuntimeError(f"vLLM 호출 오류: {e}") from e
    class _R:
        stdout = _out
        returncode = 0
    result = _R()

    raw = result.stdout.strip()
    # JSON 블록 추출
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        decision = json.loads(raw)
    except json.JSONDecodeError:
        # 파싱 실패 시 첫 번째 화이트리스트 기사 선택
        decision = {"selected_id": articles[0]["id"], "reason": "fallback", "skip": False}

    if decision.get("skip"):
        decision["article"] = None
        return decision

    selected_id = decision.get("selected_id")
    article = next((a for a in articles if a["id"] == selected_id), None)
    if article is None:
        article = articles[0]
        decision["selected_id"] = article["id"]
        decision["reason"] += " (id 불일치 → 첫 번째 선택)"
    decision["article"] = article
    return decision


if __name__ == "__main__":
    from lib.news_collector import collect
    articles = collect(hours=4)
    print(f"후보: {len(articles)}건")
    decision = select(articles)
    print(json.dumps(decision, ensure_ascii=False, indent=2))
