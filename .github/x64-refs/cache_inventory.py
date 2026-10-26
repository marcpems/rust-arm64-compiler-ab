import argparse
import hashlib
import json
from pathlib import Path


def inventory(cache, before=None):
    if not cache.is_dir() or cache.is_symlink():
        raise ValueError("Compiler-cache directory must exist and must not be a symbolic link")
    entries = []
    for path in sorted(cache.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Unexpected symbolic link in compiler cache: {path}")
        if path.is_file():
            entries.append({"path": path.relative_to(cache).as_posix(), "bytes": path.stat().st_size})
    identity = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result = {
        "files": len(entries), "bytes": sum(entry["bytes"] for entry in entries),
        "path_size_manifest_sha256": hashlib.sha256(identity).hexdigest(),
        "entries": entries,
    }
    if before is not None:
        previous = {entry["path"]: entry["bytes"] for entry in before["entries"]}
        current = {entry["path"]: entry["bytes"] for entry in entries}
        result["added_files"] = len(current.keys() - previous.keys())
        result["removed_files"] = len(previous.keys() - current.keys())
        result["resized_files"] = sum(previous[key] != current[key] for key in previous.keys() & current.keys())
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before", type=Path)
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8")) if args.before else None
    args.output.write_text(json.dumps(inventory(args.cache, before), indent=2), encoding="utf-8")
