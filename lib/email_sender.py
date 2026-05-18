"""Gmail SMTP로 영상 파일 이메일 발송."""
import os
import smtplib
import subprocess
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

# Gmail 첨부 한도(25MB) 보수적 22MB로
EMAIL_ATTACH_MB_LIMIT = 22


def _compress_for_email(src_path: Path, target_mb: float = 18.0) -> Path | None:
    """첨부 한도 초과 영상을 핸드폰 미리보기용으로 재인코딩.
    target_mb 이내로 떨어뜨림 (CRF 28, scale=1280:720). 실패 시 None."""
    try:
        import json as _json
        # 영상 길이 파악
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(src_path)],
            capture_output=True, text=True, timeout=30,
        )
        dur = float(_json.loads(r.stdout)["format"]["duration"])
        # 목표 비트레이트 (bps): target_size / duration * 0.92 (audio overhead)
        target_bps = int((target_mb * 1024 * 1024 * 8) / dur * 0.92)
        target_bps = max(target_bps, 400_000)   # 최소 400 kbps
        target_bps = min(target_bps, 1_500_000)  # 최대 1.5 Mbps

        out = Path(tempfile.gettempdir()) / f"email_{src_path.stem}.mp4"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src_path),
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", str(target_bps), "-maxrate", str(int(target_bps * 1.2)),
            "-bufsize", str(target_bps * 2),
            "-vf", "scale='min(1280,iw)':-2",
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart",
            str(out),
        ]
        subprocess.run(cmd, check=True, timeout=600)
        return out
    except Exception as e:
        print(f"  [이메일] 압축 실패: {e}")
        return None

# 환경 변수 또는 설정 파일에서 읽기
def _get_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "email.env"
    cfg = {}
    if config_path.exists():
        for line in config_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    # 환경 변수 우선
    for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"):
        if k in os.environ:
            cfg[k] = os.environ[k]
    return cfg


def send(
    subject: str,
    body: str,
    attachments: list,
    to_email: str = None,
) -> None:
    """Gmail SMTP로 이메일 발송.
    attachments: 경로 문자열 또는 (경로, 첨부파일명) 튜플 목록.
    iPhone Mail 호환을 위해 ASCII 파일명 권장."""
    cfg = _get_config()
    gmail_user = cfg.get("GMAIL_USER", "")
    gmail_pass = cfg.get("GMAIL_APP_PASSWORD", "")
    to_addr = to_email or cfg.get("NOTIFY_EMAIL", "sddari@gmail.com")

    if not gmail_user or not gmail_pass:
        raise RuntimeError(
            "Gmail 설정 없음. config/email.env 에 GMAIL_USER, GMAIL_APP_PASSWORD 설정."
        )

    msg = MIMEMultipart()
    msg["From"] = gmail_user
    msg["To"] = to_addr
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain", "utf-8"))

    total_size = 0
    attached = []
    compressed_paths = []   # 정리용 임시 파일들

    for item in attachments:
        if isinstance(item, tuple):
            fpath, fname = item
        else:
            fpath, fname = item, Path(item).name
        p = Path(fpath)
        if not p.exists():
            print(f"  [이메일] 첨부 파일 없음 (스킵): {fpath}")
            continue
        size_mb = p.stat().st_size / 1024 / 1024

        # 한도 초과면 압축 시도
        if size_mb > EMAIL_ATTACH_MB_LIMIT:
            print(f"  [이메일] {fname} {size_mb:.1f}MB → 한도 초과, 압축 시도...")
            small = _compress_for_email(p)
            if small and small.exists():
                small_mb = small.stat().st_size / 1024 / 1024
                print(f"  [이메일] 압축 완료: {small_mb:.1f}MB")
                p = small
                size_mb = small_mb
                compressed_paths.append(small)

        if total_size + size_mb > EMAIL_ATTACH_MB_LIMIT:
            print(f"  [이메일] 첨부 용량 초과 ({EMAIL_ATTACH_MB_LIMIT}MB), {fname} 스킵")
            continue

        total_size += size_mb
        part = MIMEBase("application", "octet-stream")
        part.set_payload(p.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
        msg.attach(part)
        attached.append(str(p))
        print(f"  [이메일] 첨부: {fname} ({size_mb:.1f}MB)")

    if not attached and attachments:
        # 첨부 실패 시 본문에 영상 경로 명시 (튜플 → 첫 요소만)
        paths = [item[0] if isinstance(item, tuple) else str(item) for item in attachments]
        body_extra = "\n\n[첨부 용량 초과 — 영상 위치 (NAS)]\n" + "\n".join(paths)
        msg.get_payload()[0].set_payload(body + body_extra, "utf-8")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, to_addr, msg.as_string())

    # 압축 임시 파일 정리
    for cp in compressed_paths:
        try:
            cp.unlink(missing_ok=True)
        except Exception:
            pass

    print(f"  [이메일] 발송 완료 → {to_addr}")


def send_video_notification(
    slug: str,
    ko_path: str,
    en_path: str,
    ko_title: str,
    en_title: str,
    article_title: str,
    article_source: str,
    reason: str,
    ko_meta_path: str = "",
    en_meta_path: str = "",
) -> None:
    """뉴스 영상 완성 알림 이메일."""
    subject = f"[뉴스영상] {ko_title}"

    en_exists = bool(en_path) and Path(en_path).exists()

    body_lines = [
        f"뉴스 영상이 생성되었습니다.",
        f"",
        f"KO 제목: {ko_title}",
        f"원본 기사: {article_title}",
        f"출처: {article_source}",
        f"선택 이유: {reason}",
        f"",
        f"영상 위치:",
        f"  KO: {ko_path}",
    ]
    if en_exists:
        body_lines.append(f"  EN: {en_path}  (NAS 보관, 미발송)")
    body_lines.append("")

    if ko_meta_path and Path(ko_meta_path).exists():
        body_lines += ["[KO YouTube 메타]", Path(ko_meta_path).read_text(), ""]

    body_lines += [
        "⚠️  반드시 검토 후 수동 업로드하세요.",
        "⚠️  업로드 시 '변형 또는 합성 콘텐츠' 체크 필수.",
        "",
        "─ YouTube 업로드 후 Telegram @sosig_video_bot 에서 ─",
        "/vadd https://youtu.be/여기에URL    (대기 1건일 때 자동 매칭)",
        "/vadd <번호> https://youtu.be/...   (대기 여러 건일 때)",
        "/vreject",
    ]

    body = "\n".join(body_lines)

    # 첨부: KO 영상만, 파일명은 slug 끝의 날짜(YYYYMMDD_HHMM).mp4
    import re
    m = re.search(r'(\d{8}_\d{4})$', slug)
    date_part = m.group(1) if m else "video"
    attach_name = f"{date_part}.mp4"
    send(subject=subject, body=body, attachments=[(ko_path, attach_name)])
