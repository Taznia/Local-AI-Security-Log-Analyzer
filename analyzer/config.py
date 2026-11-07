import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("TRACEWATCH_DATA", ROOT / "runtime")).resolve()
MODEL = Path(os.environ.get("TRACEWATCH_MODEL", ROOT / "models" / "Qwen3.5-2B-Q4_K_M.gguf"))
MODEL_URL = os.environ.get("TRACEWATCH_MODEL_URL", "http://127.0.0.1:8091")
CONTEXT = 2048
MAP_OUTPUT = 384
REDUCE_OUTPUT = 512
GUARD = 24
MAX_UPLOAD = 8 * 1024 * 1024
MAX_LINES = 100_000
MAX_ENTRY_BYTES = 512 * 1024
MEMORY_GUARD = 3 * 1024**3
