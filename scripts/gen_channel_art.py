"""YouTube 채널 배너 + 프로필 이미지 생성 (Flux.1-schnell).

사용:
    cd /home/sddari/news_video_poc
    .venv/bin/python scripts/gen_channel_art.py
"""
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.gpu_helper import free_gpu_for_heavy_work

import torch
from PIL import Image

NAS_OUT   = "/mnt/nas/data2/channel_art"
LOCAL_OUT = "/home/sddari/news_video_poc/work/channel_art"

BANNER_PROMPT = (
    "widescreen YouTube channel banner, professional Korean news analysis channel, "
    "dark deep navy blue and indigo gradient background, "
    "abstract glowing data visualization, floating holographic charts and graphs, "
    "subtle digital grid lines, glowing blue and cyan light streaks, "
    "cinematic panoramic composition, clean modern corporate aesthetic, "
    "no people, no text, no watermark, "
    "photorealistic, ultra high quality, Bloomberg financial news studio style"
)

PROFILE_PROMPT = (
    "square YouTube channel profile icon, "
    "dark navy blue background, centered abstract glowing emblem, "
    "stylized letter N made of light trails and data streams, "
    "electric blue and cyan accent colors, "
    "clean minimalist corporate design, sharp crisp edges, "
    "no text, high contrast, perfect symmetry"
)


def main():
    Path(NAS_OUT).mkdir(parents=True, exist_ok=True)
    Path(LOCAL_OUT).mkdir(parents=True, exist_ok=True)

    # 1) GPU 사전 정리 (ollama 모델 언로드)
    free_gpu_for_heavy_work()

    # 2) Flux 로드
    print("\nFlux.1-schnell 로드 중...")
    from diffusers import FluxPipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipe = FluxPipeline.from_pretrained(
        "black-forest-labs/FLUX.1-schnell",
        torch_dtype=torch.bfloat16,
    )
    pipe = pipe.to(device)
    print(f"로드 완료 (device={device})")

    # 3) 배너 (1360x768 → 2048x1152 업스케일)
    print("\n[1/2] 배너 이미지 생성 중...")
    banner_raw = pipe(
        BANNER_PROMPT,
        height=768, width=1360,
        num_inference_steps=4,
        guidance_scale=0.0,
        max_sequence_length=256,
    ).images[0]

    banner = banner_raw.resize((2048, 1152), Image.LANCZOS)
    for path in [f"{LOCAL_OUT}/banner.png", f"{NAS_OUT}/banner.png"]:
        banner.save(path, optimize=True)
    size = Path(f"{NAS_OUT}/banner.png").stat().st_size
    print(f"  저장: {NAS_OUT}/banner.png  ({size/1024/1024:.1f} MB)")

    # 4) 프로필 (800x800)
    print("\n[2/2] 프로필 사진 생성 중...")
    profile = pipe(
        PROFILE_PROMPT,
        height=800, width=800,
        num_inference_steps=4,
        guidance_scale=0.0,
        max_sequence_length=256,
    ).images[0]

    for path in [f"{LOCAL_OUT}/profile.png", f"{NAS_OUT}/profile.png"]:
        profile.save(path, optimize=True)
    size = Path(f"{NAS_OUT}/profile.png").stat().st_size
    print(f"  저장: {NAS_OUT}/profile.png  ({size/1024/1024:.1f} MB)")

    # 5) 정리
    del pipe
    if device == "cuda":
        torch.cuda.empty_cache()

    print("\n✅ 완료")


if __name__ == "__main__":
    main()
