import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import json


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs)


with tempfile.TemporaryDirectory() as work:
    original = os.getcwd()
    os.chdir(work)
    try:
        os.environ.pop("SCCACHE_EXTRAFILES", None)
        clang = shutil.which("clang-cl")
        profdata = Path(clang).with_name("llvm-profdata.exe")
        Path("test.c").write_text("int main(int argc, char **argv) { return argc > 1; }\n")
        run(clang, "/O2", "-clang:-fprofile-generate", "test.c", "/Fetrain.exe")
        for n in (1, 2):
            run(str(Path("train.exe").resolve()), env=dict(os.environ, LLVM_PROFILE_FILE=f"{n}.profraw"))
            run(profdata, "merge", *[f"{i}.profraw" for i in range(1, n + 1)], "-o", f"{n}.profdata")
        assert Path("1.profdata").read_bytes() != Path("2.profdata").read_bytes()
        shutil.copyfile("1.profdata", "use.profdata")
        for label, hits, misses in [("cold", 0, 1), ("unchanged", 1, 0), ("replaced", 0, 1)]:
            if label == "replaced":
                shutil.copyfile("2.profdata", "use.profdata")
            Path("use.obj").unlink(missing_ok=True)
            run("sccache", "--zero-stats")
            run("sccache", clang, "/c", "/O2", "-fprofile-use=use.profdata", "test.c", "/Fouse.obj")
            stats = json.loads(run("sccache", "--show-stats", "--stats-format=json"))["stats"]
            actual = tuple(sum(stats[key]["counts"].values()) for key in ("cache_hits", "cache_misses"))
            print(f"{label}: hits={actual[0]}, misses={actual[1]}", flush=True)
            assert actual == (hits, misses) and Path("use.obj").is_file()
    finally:
        os.chdir(original)
