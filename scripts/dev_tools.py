"""Optional portable Git and gh installation inside ignored bin/dev-tools/."""
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import download, get_json

root = Path(__file__).resolve().parents[1] / "bin" / "dev-tools"
root.mkdir(parents=True, exist_ok=True)
for repo, match, dest in [("git-for-windows/git", lambda n: n.startswith("MinGit-") and n.endswith("64-bit.zip") and "busybox" not in n, "git"), ("cli/cli", lambda n: n.endswith("windows_amd64.zip"), "gh")]:
    release = get_json(f"https://api.github.com/repos/{repo}/releases/latest")
    asset = next(a for a in release["assets"] if match(a["name"]))
    target = root / asset["name"]
    download(asset["browser_download_url"], target)
    folder = root / dest
    folder.mkdir(exist_ok=True)
    with zipfile.ZipFile(target) as archive:
        archive.extractall(folder)
    print(dest, release["tag_name"], flush=True)
