"""Fetch one pinned MIT-licensed Microsoft Rocketbox character, not the full library."""
from pathlib import Path
from urllib.request import urlopen
import hashlib
import json

REVISION = "0943055db6ec570bcef9f2c8b41c9e5467c808f9"
BASE = f"https://raw.githubusercontent.com/microsoft/Microsoft-Rocketbox/{REVISION}"
CHARACTER = "Assets/Avatars/Professions/Medical_Male_03"
FILES = ["Export/Medical_Male_03.fbx"] + [
    f"Textures/m153_{part}_{kind}.tga"
    for part, kinds in [("body", ["color", "normal", "specular"]),
                        ("head", ["color", "normal", "specular"]),
                        ("stetoskop", ["color", "normal"])]
    for kind in kinds
]


def main():
    dest = Path(__file__).resolve().parents[1] / "Assets/ThirdParty/Rocketbox"
    dest.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name in FILES:
        url = f"{BASE}/{CHARACTER}/{name}"
        target = dest / Path(name).name
        if not target.exists():
            with urlopen(url, timeout=120) as response:
                data = response.read()
            if data.startswith(b"version https://git-lfs"):
                raise RuntimeError("Unexpected Git LFS pointer; asset was not downloaded")
            target.write_bytes(data)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest.append({"file": target.name, "source": url, "sha256": digest})
        print(f"Ready: {target.name} ({target.stat().st_size:,} bytes)")
    manifest_path = dest / "sources.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise RuntimeError("Asset hashes differ from the checked-in manifest")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
