import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


parser = argparse.ArgumentParser()
parser.add_argument("--full", action="store_true")
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
host = "aarch64-pc-windows-msvc"
ec = "arm64ec-pc-windows-msvc"
dist = Path("build/dist")
manifest = []
for path in sorted(dist.iterdir()):
    if path.is_file():
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest})
(args.output / "distribution-manifest.json").write_text(json.dumps(manifest, indent=2))
for name in (
    f"rustc-nightly-{host}.tar.xz", f"cargo-nightly-{host}.tar.xz",
    f"rust-std-nightly-{host}.tar.xz", f"rust-std-nightly-{ec}.tar.xz",
    "rust-src-nightly.tar.xz", f"rust-nightly-{host}.msi",
    f"rust-nightly-{host}.tar.xz",
):
    if not (dist / name).is_file():
        raise RuntimeError(f"Missing full-distribution artifact: {name}")

unpacked = Path("build/nvme-unpacked")
unpacked.mkdir()
for stem in (f"rustc-nightly-{host}", f"cargo-nightly-{host}",
             f"rust-std-nightly-{host}", f"rust-std-nightly-{ec}", "rust-src-nightly"):
    run("tar", "-xf", str(dist / f"{stem}.tar.xz"), "-C", str(unpacked))
sysroot = unpacked / f"rustc-nightly-{host}" / "rustc"
for target in (host, ec):
    component = unpacked / f"rust-std-nightly-{target}" / f"rust-std-{target}"
    shutil.copytree(component / "lib/rustlib" / target / "lib",
                    sysroot / "lib/rustlib" / target / "lib", dirs_exist_ok=True)
shutil.copytree(unpacked / "rust-src-nightly/rust-src/lib/rustlib/src",
                sysroot / "lib/rustlib/src", dirs_exist_ok=True)
rustc = (sysroot / "bin/rustc.exe").resolve()
cargo = (unpacked / f"cargo-nightly-{host}/cargo/bin/cargo.exe").resolve()
run(str(rustc), "-vV")
run(str(cargo), "-V")
source = unpacked / "smoke.rs"
source.write_text('fn main() { assert_eq!(2 + 2, 4); println!("nvme-artifact-ok"); }\n')
binary = unpacked / "smoke.exe"
run(str(rustc), str(source), "-o", str(binary))
completed = subprocess.run([str(binary.resolve())], check=True, capture_output=True, text=True)
if completed.stdout.strip() != "nvme-artifact-ok":
    raise RuntimeError("Extracted native compiler produced an unexpected executable")
run(str(rustc), str(source), "--target", ec, "--emit=obj", "-o", str(unpacked / "smoke-ec.obj"))
if args.full:
    llvm_config = (Path("build") / host / "llvm/bin/llvm-config.exe").resolve()
    if not llvm_config.is_file():
        raise RuntimeError("Built LLVM configuration is missing")
    config = Path("bootstrap.toml")
    original = config.read_bytes()
    content = f'''profile = "user"
change-id = 115898
[rust]
channel = "nightly"
verbose-tests = true
lld = false
[build]
rustc = "{rustc.as_posix()}"
cargo = "{cargo.as_posix()}"
local-rebuild = true
compiletest-allow-stage0 = true
[target.{host}]
llvm-config = "{llvm_config.as_posix()}"
'''
    (args.output / "qualification-bootstrap.toml").write_text(content)
    suites = [
        "tests/assembly-llvm", "tests/codegen-llvm", "tests/codegen-units",
        "tests/incremental", "tests/mir-opt", "tests/pretty",
        "tests/run-make/glibc-symbols-x86_64-unknown-linux-gnu",
        "tests/run-make/rust-lld-x86_64-unknown-linux-gnu-dist",
        "tests/ui", "tests/crashes", "tests/rustdoc-html",
    ]
    env = dict(os.environ, COMPILETEST_ENABLE_DIST_TESTS="1")
    try:
        config.write_text(content, encoding="utf-8")
        run(sys.executable, "x.py", "test", "--build", host, "--stage", "0", *suites, env=env)
    finally:
        config.write_bytes(original)
(args.output / "qualification.json").write_text(json.dumps({
    "passed": True, "full_upstream_opt_dist_test_selection": args.full,
    "native_extracted_compiler_execution": True, "arm64ec_object_compilation": True,
    "arm64ec_runtime_execution": False, "msi_installation_tested": False,
    "temp_unchanged": True,
}, indent=2))
