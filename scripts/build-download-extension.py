"""Generate a Chrome extension limited to the supplied dashboard origin (no dependencies)."""
import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def build(origin: str, output: Path):
    url = urlsplit(origin)
    if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment or "*" in origin
            or (url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1"))):
        raise ValueError("HTTPS 서비스 원점 또는 http://localhost:포트를 지정하세요. 경로는 넣지 마세요.")
    source = ROOT / "extensions" / "ssu-downloader"
    output = output.resolve()
    if output == source or source in output.parents:
        raise ValueError("소스 폴더 밖에 빌드하세요.")
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["content_scripts"][0]["matches"] = [f"{url.scheme}://{url.netloc}/*"]
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    assets = ["worker.js", "bridge.js", "manager.html", "manager.js", "manager.css", "patrick-drool.jpg",
              "offscreen.html", "offscreen.js", "audio-extract.mjs", "vendor/mp4box.all.mjs",
              "vendor/rolldown-runtime-w6R9maHv.mjs", "vendor/styp-9TIZZDLN.mjs", *manifest["icons"].values()]
    for name in assets:
        (output / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, output / name)
    with ZipFile(output.with_suffix(".zip"), "w") as archive:
        for name in ["manifest.json", *assets]:
            archive.write(output / name, name)
    print(f"Chrome에서 압축해제된 확장 프로그램으로 로드: {output}")
    print(f"배포 패키지: {output.with_suffix('.zip')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", nargs="?", default="https://daunload.cloud", help="서비스 원점 (기본: https://daunload.cloud)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "ssu-downloader")
    args = parser.parse_args()
    build(args.origin, args.out)
