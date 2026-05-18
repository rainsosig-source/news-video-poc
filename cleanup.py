"""NAS 영상 + 로컬 work 디렉토리 7일 보관 정책 적용.

크론에서 매일 새벽 3시 실행 권장:
  0 3 * * * /home/sddari/news_video_poc/.venv/bin/python /home/sddari/news_video_poc/cleanup.py
"""
import os
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

NAS_OUT   = "/mnt/nas/data2/mov/news_video_poc"
WORK_BASE = Path(__file__).parent / "work"
LOG_DIR   = Path(__file__).parent / "logs"
RETAIN_DAYS = 7
KEEP_RECENT_BACKUPS = 3   # work_backup_*는 최근 N개만 유지 (날짜 무관)
DRY_RUN = "--dry-run" in sys.argv


def _mtime(p: Path) -> datetime:
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)


def _is_old(p: Path, cutoff: datetime) -> bool:
    try:
        return _mtime(p) < cutoff
    except Exception:
        return False


def cleanup_nas() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETAIN_DAYS)
    nas = Path(NAS_OUT)
    if not nas.exists():
        print(f"NAS 경로 없음 (마운트 안됨?): {NAS_OUT}")
        return

    deleted = 0
    for subdir in ["ko", "en"]:
        d = nas / subdir
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file() and _is_old(f, cutoff):
                print(f"  삭제 {'(dry)' if DRY_RUN else ''}: {f}")
                if not DRY_RUN:
                    f.unlink()
                deleted += 1
    print(f"NAS 삭제: {deleted}건")


def cleanup_work() -> None:
    """work_backup_* 는 최근 N개만 유지, 그 외 work_*는 7일 보관.
    work_backup_*는 프로젝트 루트와 work/ 둘 다 검색 (위치 일관성 없는 이력 흡수)."""
    project_root = WORK_BASE.parent
    if not project_root.exists():
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETAIN_DAYS)

    # work_backup_* 두 위치 모두 검색
    backups = []
    for base in [project_root, WORK_BASE]:
        if base.exists():
            backups.extend(d for d in base.glob("work_backup_*") if d.is_dir())
    backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    backup_deleted = 0
    for d in backups[KEEP_RECENT_BACKUPS:]:
        print(f"  백업 삭제 {'(dry)' if DRY_RUN else ''}: {d}")
        if not DRY_RUN:
            shutil.rmtree(d)
        backup_deleted += 1
    print(f"work_backup 정리: {len(backups)}개 중 {backup_deleted}개 삭제 (최근 {KEEP_RECENT_BACKUPS}개 유지)")

    # 그 외 work_* (work_done, work_failed 등)은 7일 보관
    other_deleted = 0
    for d in project_root.glob("work_*"):
        if d.is_dir() and not d.name.startswith("work_backup_") and _is_old(d, cutoff):
            print(f"  삭제 {'(dry)' if DRY_RUN else ''}: {d}")
            if not DRY_RUN:
                shutil.rmtree(d)
            other_deleted += 1
    print(f"work 기타 디렉토리 삭제: {other_deleted}건")


def cleanup_logs() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETAIN_DAYS)
    if not LOG_DIR.exists():
        return
    deleted = 0
    for f in LOG_DIR.glob("auto_*.log"):
        if _is_old(f, cutoff):
            print(f"  삭제 {'(dry)' if DRY_RUN else ''}: {f}")
            if not DRY_RUN:
                f.unlink()
            deleted += 1
    print(f"로그 삭제: {deleted}건")


def cleanup_work_subdirs() -> None:
    """run_auto.py가 생성한 work/<slug>/ 디렉토리 7일 보관."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETAIN_DAYS)
    base = Path(__file__).parent / "work"
    if not base.exists():
        return
    deleted = 0
    for d in base.iterdir():
        if d.is_dir() and "_2026" in d.name and _is_old(d, cutoff):
            print(f"  삭제 {'(dry)' if DRY_RUN else ''}: {d}")
            if not DRY_RUN:
                shutil.rmtree(d)
            deleted += 1
    print(f"work 슬러그 디렉토리 삭제: {deleted}건")


if __name__ == "__main__":
    print(f"=== cleanup.py (dry_run={DRY_RUN}, retain={RETAIN_DAYS}d) ===")
    cleanup_nas()
    cleanup_work()
    cleanup_work_subdirs()
    cleanup_logs()
    print("완료")
