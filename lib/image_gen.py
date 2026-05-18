"""Flux.1-dev 이미지 생성 (1회 로드, 한/영 공유). 품질 우선."""
import os
import torch
from pathlib import Path

MODEL_ID = "black-forest-labs/FLUX.1-dev"
NUM_STEPS = 28          # Dev: 28~50 권장 (28이 품질/시간 균형점)
GUIDANCE = 3.5          # Dev는 CFG 필요 (Schnell은 0.0)
# 생성 해상도 — 1024x576 (16:9, /16 호환)
# parallax/compose에서 1920x1080으로 업스케일. GB10에서 step당 ~10초 → 이미지당 ~5분.
GEN_WIDTH = 1024
GEN_HEIGHT = 576


def generate(prompts: list[dict], images_dir: str) -> list[dict]:
    """프롬프트 리스트 → PNG 파일 생성. 이미 있으면 스킵."""
    from diffusers import FluxPipeline

    Path(images_dir).mkdir(parents=True, exist_ok=True)

    todo = [p for p in prompts if not os.path.exists(os.path.join(images_dir, p["image_file"]))]
    if not todo:
        print(f"  이미지 모두 캐시됨 ({len(prompts)}장)")
        return prompts

    print(f"  {MODEL_ID} 로드 중 ({len(todo)}/{len(prompts)}장 생성 예정)...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16

    pipe = FluxPipeline.from_pretrained(MODEL_ID, torch_dtype=dtype)
    try:
        pipe = pipe.to(device)
    except Exception:
        print("  VRAM 부족 → cpu_offload 폴백")
        pipe.enable_model_cpu_offload()

    for item in todo:
        out_path = os.path.join(images_dir, item["image_file"])
        print(f"  [{item['idx']+1}/{len(prompts)}] {item['prompt'][:60]}...")
        image = pipe(
            item["prompt"],
            height=GEN_HEIGHT,
            width=GEN_WIDTH,
            num_inference_steps=NUM_STEPS,
            guidance_scale=GUIDANCE,
            max_sequence_length=512,
        ).images[0]
        image.save(out_path)
        print(f"    → {out_path}")

    del pipe
    if device == "cuda":
        torch.cuda.empty_cache()

    return prompts


if __name__ == "__main__":
    import json
    prompts = json.loads(open("work/prompts.json").read())
    generate(prompts, "work/images")
