import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def qualify(output):
    release = json.loads((output / "release.json").read_text())
    successful = {event["section"] for event in release["sections"] if event["status"] == "OK"}
    required = {
        "Stage 1 (Rustc + rustdoc + cargo + clippy PGO)",
        "Stage 2 (LLVM PGO)", "Stage 5 (final build)", "Run tests",
    }
    if release["exit_code"] != 0 or release["timed_out"] or not required <= successful:
        raise RuntimeError("Full PGO release and upstream artifact tests did not complete")
    host = "x86_64-pc-windows-msvc"
    dist = Path("build/dist")
    for stem in ("rustc", "cargo", "rust-std", "rustc-codegen-cranelift", "rust"):
        if not (dist / f"{stem}-nightly-{host}.tar.xz").is_file():
            raise RuntimeError(f"Missing distribution component: {stem}")
    for name in ("rust-src-nightly.tar.xz", f"rust-nightly-{host}.msi"):
        if not (dist / name).is_file():
            raise RuntimeError(f"Missing distribution artifact: {name}")
    manifest = []
    for path in sorted(dist.iterdir()):
        if path.is_file():
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            manifest.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest})
    (output / "distribution-manifest.json").write_text(json.dumps(manifest, indent=2))
    unpacked = Path("build/unpacked-dist")
    compiler = unpacked / f"rustc-nightly-{host}/rustc/bin/rustc.exe"
    cargo = unpacked / f"cargo-nightly-{host}/cargo/bin/cargo.exe"
    subprocess.run([str(compiler.resolve()), "-vV"], check=True)
    subprocess.run([str(cargo.resolve()), "-V"], check=True)
    source = output / "smoke.rs"
    source.write_text('fn main() { assert_eq!(2 + 2, 4); println!("refs-artifact-ok"); }\n')
    binary = output / "smoke.exe"
    subprocess.run([str(compiler.resolve()), str(source.resolve()), "-o", str(binary.resolve())], check=True)
    result = subprocess.run([str(binary.resolve())], check=True, capture_output=True, text=True)
    if result.stdout.strip() != "refs-artifact-ok":
        raise RuntimeError("Extracted compiler execution failed")
    profiles = [{"name": str(path), "bytes": path.stat().st_size}
                for path in Path("opt-artifacts").rglob("*.profdata")]
    if not profiles or any(profile["bytes"] == 0 for profile in profiles):
        raise RuntimeError("Missing or empty PGO profiles")
    (output / "qualification.json").write_text(json.dumps({
        "passed": True, "full_upstream_opt_dist_tests": True,
        "native_extracted_compiler_execution": True, "msi_installation_tested": False,
        "profiles": profiles,
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    qualify(parser.parse_args().output)
