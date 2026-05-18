"""자동화 실행 상태 관리: 24h 이력 R/W."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

STATE_FILE = Path(__file__).parent.parent / "work" / "auto_state.json"


def _load() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"runs": []}


def _save(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_covered_titles(hours: float = 24.0) -> list[str]:
    """최근 hours 시간 내 이미 제작된 영상의 제목 목록."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    state = _load()
    titles = []
    for run in state.get("runs", []):
        try:
            ts = datetime.fromisoformat(run["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff and not run.get("skipped"):
                titles.append(run.get("title", ""))
        except Exception:
            pass
    return [t for t in titles if t]


def record_run(
    title: str,
    slug: str,
    skipped: bool = False,
    skip_reason: str = "",
    ko_path: str = "",
    en_path: str = "",
) -> None:
    """실행 기록 저장."""
    state = _load()
    state.setdefault("runs", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "slug": slug,
        "skipped": skipped,
        "skip_reason": skip_reason,
        "ko_path": ko_path,
        "en_path": en_path,
    })
    # 7일 이상 오래된 기록 정리
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    state["runs"] = [
        r for r in state["runs"]
        if _parse_ts(r.get("timestamp", "")) >= cutoff
    ]
    _save(state)


def _parse_ts(ts_str: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def get_recent_runs(n: int = 10) -> list[dict]:
    state = _load()
    return list(reversed(state.get("runs", [])))[:n]


if __name__ == "__main__":
    import json
    print("최근 실행:")
    for r in get_recent_runs():
        print(f"  {r['timestamp'][:16]}  {r['title'][:40]}  skip={r['skipped']}")
    print("\n오늘 다뤄진 주제:")
    for t in get_covered_titles():
        print(f"  {t}")
