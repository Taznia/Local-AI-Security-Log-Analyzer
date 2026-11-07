"""One-time network setup. Runtime never needs network access."""
import argparse
import hashlib
import json
import platform
import sys
import urllib.request
import zipfile
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_REPO = "unsloth/Qwen3.5-2B-GGUF"
MODEL_FILE = "Qwen3.5-2B-Q4_K_M.gguf"
MODEL_REVISION = "f6d5376be1edb4d416d56da11e5397a961aca8ae"


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Tracewatch-setup/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def download(url, path):
    if path.exists():
        print(f"Already present: {path.name}", flush=True)
        return
    part = path.with_suffix(path.suffix + ".part")
    print(f"Downloading {path.name}", flush=True)
    for attempt in range(12):
        offset = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": "Tracewatch-setup/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as src:
                mode = "ab" if offset and src.status == 206 else "wb"
                length = src.headers.get("Content-Length")
                expected = (offset if mode == "ab" else 0) + int(length) if length else None
                with part.open(mode) as dst:
                    while block := src.read(256 * 1024):
                        dst.write(block)
                if expected is not None and part.stat().st_size != expected:
                    raise OSError("Incomplete download; resuming retained bytes")
            break
        except (OSError, TimeoutError) as exc:
            print(f"Retry {attempt + 1}: {type(exc).__name__}; retaining partial download", flush=True)
            if attempt == 11:
                raise
            time.sleep(2)
    part.replace(path)
    print(f"Saved {path.name}: {path.stat().st_size / 1024**2:.1f} MiB", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", default="b12182", help="Pinned llama.cpp release tag")
    args = parser.parse_args()
    for name in ("models", "bin"):
        (ROOT / name).mkdir(exist_ok=True)
    release = get_json("https://api.github.com/repos/ggml-org/llama.cpp/releases/" + ("tags/" + args.release if args.release else "latest"))
    needle = "win-cpu-x64" if sys.platform == "win32" else "ubuntu-x64"
    candidates = [a for a in release["assets"] if needle in a["name"] and a["name"].endswith((".zip", ".tar.gz"))]
    if not candidates:
        raise SystemExit("No matching prebuilt binary. Build llama-server from source and place it in bin/; see README.")
    asset = candidates[0]
    archive = ROOT / "bin" / asset["name"]
    download(asset["browser_download_url"], archive)
    expected_archive = asset.get("digest", "")
    if expected_archive and expected_archive.startswith("sha256:"):
        with archive.open("rb") as source:
            actual_archive = hashlib.file_digest(source, "sha256").hexdigest()
        if actual_archive != expected_archive.split(":", 1)[1]:
            raise SystemExit("Runtime archive checksum mismatch; remove the archive and retry setup.")
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for item in z.infolist():
                target = (ROOT / "bin" / item.filename).resolve()
                if not target.is_relative_to((ROOT / "bin").resolve()):
                    raise ValueError("Invalid archive path")
            z.extractall(ROOT / "bin")
    else:
        with tarfile.open(archive) as tar:
            tar.extractall(ROOT / "bin", filter="data")
    if sys.platform != "win32":
        for binary in (ROOT / "bin").rglob("llama-server"):
            binary.chmod(binary.stat().st_mode | 0o111)
    model_info = get_json(f"https://huggingface.co/api/models/{MODEL_REPO}/revision/{MODEL_REVISION}?blobs=true")
    revision = model_info["sha"]
    if MODEL_FILE not in [s["rfilename"] for s in model_info["siblings"]]:
        raise SystemExit(f"Model file unavailable: {MODEL_FILE}")
    model = ROOT / "models" / MODEL_FILE
    download(f"https://huggingface.co/{MODEL_REPO}/resolve/{revision}/{MODEL_FILE}?download=true", model)
    with model.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    expected_model = next(s for s in model_info["siblings"] if s["rfilename"] == MODEL_FILE).get("lfs", {}).get("sha256")
    if expected_model and digest != expected_model:
        raise SystemExit("Model checksum mismatch; remove the model file and retry setup.")
    manifest = {"model_repo": MODEL_REPO, "model_file": MODEL_FILE, "model_revision": revision,
                "model_sha256": digest, "model_bytes": model.stat().st_size,
                "llama_release": release["tag_name"], "binary_asset": asset["name"],
                "platform": platform.platform()}
    (ROOT / "models" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (ROOT / "setup-manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
