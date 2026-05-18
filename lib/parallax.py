"""2.5D 깊이 패럴랙스 클립 생성 (Depth-Anything-V2 + PyTorch grid_sample).

이미지 한 장 → 깊이 추정 → 각 프레임에서 깊이별로 픽셀 시프트 → 시네마틱 카메라 이동.
ffmpeg에는 raw RGB 프레임을 pipe로 보내서 H.264 인코딩.

호환성: compose._ken_burns_clip 와 동일한 시그니처 (image_path, duration, out_path).
실패 시 호출자 측에서 ken_burns로 폴백할 수 있도록 RuntimeError 발생.
"""
import math
import random
import subprocess
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

# 출력 영상 규격 (compose.py와 일치해야 함)
W, H, FPS = 1920, 1080, 30

# 카메라 이동 강도 (시청자가 명확히 인지할 수 있는 수준)
MAX_DISP_PX = 90.0      # 가장 가까운 픽셀의 최대 시프트 (1920의 약 4.7%)
ZOOM_START = 1.05
ZOOM_END = 1.20         # 줌 범위 확대 (5% → 20%)
BATCH_FRAMES = 30        # GPU 메모리 vs 처리량 균형

# Depth model — 한 번만 로드 (모듈 전역 캐시)
_depth_pipe = None
_depth_device = None


def _get_depth_pipeline():
    global _depth_pipe, _depth_device
    if _depth_pipe is not None:
        return _depth_pipe, _depth_device
    from transformers import pipeline as hf_pipeline
    _depth_device = "cuda" if torch.cuda.is_available() else "cpu"
    _depth_pipe = hf_pipeline(
        task="depth-estimation",
        model="depth-anything/Depth-Anything-V2-Small-hf",
        device=_depth_device,
    )
    return _depth_pipe, _depth_device


def _estimate_depth(pil_img: Image.Image) -> torch.Tensor:
    """이미지 → 정규화된 depth 텐서 (1, 1, H, W). 가까울수록 1.0, 멀수록 0.0."""
    pipe, device = _get_depth_pipeline()
    out = pipe(pil_img)
    depth_pil = out["depth"]  # PIL 'L' (0~255, 가까울수록 큼 — 모델별로 다를 수 있음)
    arr = np.asarray(depth_pil).astype(np.float32) / 255.0
    # H, W → (1,1,H,W)로 변환 후 출력 해상도(H,W)로 리사이즈
    t = torch.from_numpy(arr)[None, None].to(device)
    if t.shape[-2:] != (H, W):
        t = F.interpolate(t, size=(H, W), mode="bilinear", align_corners=False)
    # 정규화 (안정 범위로)
    mn, mx = t.min(), t.max()
    if (mx - mn) > 1e-6:
        t = (t - mn) / (mx - mn)
    return t  # (1,1,H,W) on device


def _camera_motion(t: float, mode: str) -> tuple[float, float, float]:
    """프레임 시점 t∈[0,1] → (offset_x_norm, offset_y_norm, zoom).
    norm 좌표는 [-1,1] (grid_sample 좌표계와 동일)."""
    # 0~1 → -1~+1 사인파 매끄럽게
    s = math.sin(math.pi * (t - 0.5))  # -1 → +1
    base_zoom = ZOOM_START + (ZOOM_END - ZOOM_START) * t

    # 정규화된 시프트량: pixel → grid 좌표
    px2gx = 2.0 / W
    px2gy = 2.0 / H
    max_x = MAX_DISP_PX * px2gx
    max_y = MAX_DISP_PX * px2gy

    if mode == "pan_lr":
        return s * max_x, 0.0, base_zoom
    if mode == "pan_rl":
        return -s * max_x, 0.0, base_zoom
    if mode == "pan_ud":
        return 0.0, s * max_y, base_zoom
    if mode == "pan_du":
        return 0.0, -s * max_y, base_zoom
    if mode == "diag_dr":
        return s * max_x * 0.7, s * max_y * 0.7, base_zoom
    if mode == "diag_ul":
        return -s * max_x * 0.7, -s * max_y * 0.7, base_zoom
    # 기본 zoom-in
    return 0.0, 0.0, base_zoom


def _pick_motion_mode(idx: int) -> str:
    """이미지 순서별로 다양한 카메라 모드 분배 — 단조로움 방지."""
    modes = ["pan_lr", "pan_rl", "pan_ud", "diag_dr", "pan_du", "diag_ul"]
    return modes[idx % len(modes)]


def generate_parallax_clip(
    image_path: str,
    duration: float,
    out_path: str,
    motion_idx: int = 0,
) -> None:
    """이미지 → 깊이 패럴랙스 mp4 클립."""
    duration = max(duration, 1.0)
    n_frames = int(round(duration * FPS))
    mode = _pick_motion_mode(motion_idx)

    pil = Image.open(image_path).convert("RGB")
    if pil.size != (W, H):
        pil = pil.resize((W, H), Image.LANCZOS)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # 이미지를 GPU 텐서로 (1,3,H,W) [-1,1] 범위 (grid_sample용 [0,1] 대신 raw 0-255 fp32 사용)
    img_np = np.asarray(pil).astype(np.float32)  # (H,W,3)
    img_t = torch.from_numpy(img_np).permute(2, 0, 1)[None].to(device)  # (1,3,H,W)

    # 깊이 — 가까울수록 1.0
    depth = _estimate_depth(pil)  # (1,1,H,W) on device

    # base meshgrid (정규화 -1..1)
    yy, xx = torch.meshgrid(
        torch.linspace(-1.0, 1.0, H, device=device),
        torch.linspace(-1.0, 1.0, W, device=device),
        indexing="ij",
    )
    base_grid = torch.stack([xx, yy], dim=-1)[None]  # (1,H,W,2)

    # ffmpeg 파이프 인코더
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "pipe:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "medium", "-crf", "18",
        "-r", str(FPS), out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    try:
        # 배치 단위로 프레임 생성 → ffmpeg에 stream
        for batch_start in range(0, n_frames, BATCH_FRAMES):
            batch_end = min(batch_start + BATCH_FRAMES, n_frames)
            bsz = batch_end - batch_start
            if bsz <= 0:
                break

            # 각 프레임의 카메라 파라미터 텐서
            ts = [
                (i / max(n_frames - 1, 1)) for i in range(batch_start, batch_end)
            ]
            ox = torch.zeros(bsz, device=device)
            oy = torch.zeros(bsz, device=device)
            zoom = torch.zeros(bsz, device=device)
            for k, t in enumerate(ts):
                _ox, _oy, _z = _camera_motion(t, mode)
                ox[k] = _ox
                oy[k] = _oy
                zoom[k] = _z

            # 카메라 이동 적용된 샘플링 좌표 만들기
            #   grid' = base_grid / zoom + camera_offset + depth_parallax
            #   depth_parallax: 가까운 곳(d=1)이 더 많이 시프트
            grids = []
            for k in range(bsz):
                z = zoom[k].item()
                _ox = ox[k].item()
                _oy = oy[k].item()
                # depth-based parallax shift (방향: pan과 같은 방향, 강도는 깊이 비례)
                # 정규화 시프트 단위: 1px = 2/W (x), 2/H (y)
                # 패럴랙스 강도: 가까운 픽셀이 카메라와 반대로 강하게 움직이도록
                # 실제 값은 _ox, _oy 자체로 충분 (이미 시간 함수)
                dx = depth * _ox
                dy = depth * _oy
                # base_grid를 zoom으로 줄이기 + offset
                gx = base_grid[..., 0] / z + dx[0, 0]
                gy = base_grid[..., 1] / z + dy[0, 0]
                grid = torch.stack([gx, gy], dim=-1)  # (1,H,W,2)
                grids.append(grid)
            grid_batch = torch.cat(grids, dim=0)  # (bsz,H,W,2)

            # 이미지를 bsz개로 복제해서 한 번에 grid_sample
            img_batch = img_t.expand(bsz, -1, -1, -1)
            warped = F.grid_sample(
                img_batch, grid_batch,
                mode="bilinear", padding_mode="reflection", align_corners=True,
            )  # (bsz,3,H,W)

            # uint8로 변환 후 numpy
            warped = warped.clamp(0, 255).to(torch.uint8)
            frames = warped.permute(0, 2, 3, 1).contiguous().cpu().numpy()  # (bsz,H,W,3)
            proc.stdin.write(frames.tobytes())

        proc.stdin.close()
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg pipe encode rc={rc}")
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        raise

    if device == "cuda":
        torch.cuda.empty_cache()


def release():
    """깊이 모델 메모리 해제 (GPU 다른 작업 전 호출)."""
    global _depth_pipe
    _depth_pipe = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


if __name__ == "__main__":
    import sys
    img = sys.argv[1] if len(sys.argv) > 1 else "work/images/00.png"
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/parallax_test.mp4"
    dur = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
    print(f"depth+parallax: {img} → {out} ({dur}s)")
    generate_parallax_clip(img, dur, out, motion_idx=0)
    print(f"OK → {out}")
