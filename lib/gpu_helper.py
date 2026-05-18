"""GPU 메모리 헬퍼: 이미지 생성 전 ollama 모델 언로드."""
import time
import requests

OLLAMA_BASE = "http://localhost:11434"


def get_loaded_models() -> list[dict]:
    """현재 ollama에 로드된 모델 목록 (이름 + VRAM 사용량)."""
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/ps", timeout=5)
        return r.json().get("models", [])
    except Exception:
        return []


def unload_ollama_model(model_name: str) -> bool:
    """특정 ollama 모델을 즉시 언로드 (keep_alive=0)."""
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": model_name, "prompt": "", "keep_alive": 0},
            timeout=30,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  ! ollama 언로드 실패 ({model_name}): {e}")
        return False


def free_gpu_for_heavy_work(wait_sec: int = 3) -> None:
    """이미지 생성/Flux 등 GPU 집약 작업 전 ollama 모델 모두 언로드.
    NVIDIA 드라이버가 메모리 회수할 시간을 위해 wait_sec만큼 대기.
    """
    loaded = get_loaded_models()
    if not loaded:
        print("GPU 정리: 언로드할 ollama 모델 없음")
        return

    total_vram = sum(m.get("size_vram", 0) for m in loaded) / 1024**3
    print(f"GPU 정리: ollama 모델 {len(loaded)}개 언로드 ({total_vram:.1f} GB)")
    for m in loaded:
        if unload_ollama_model(m["name"]):
            vram_gb = m.get("size_vram", 0) / 1024**3
            print(f"  ✓ {m['name']} ({vram_gb:.1f} GB)")

    print(f"  드라이버 회수 대기 {wait_sec}초...")
    time.sleep(wait_sec)
