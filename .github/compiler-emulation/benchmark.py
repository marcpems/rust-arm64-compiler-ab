import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time


VERSION = "1.99.1"
SOURCE = "4649aa9700619f94cf9c66876e9549d83420e16c"
TRIPLES = {
    "arm64": "aarch64-pc-windows-msvc",
    "x64": "x86_64-pc-windows-msvc",
}
MACHINES = {"arm64": 0xAA64, "x64": 0x8664}
CASES = {
    "arm-native": ("arm64", "arm64"),
    "arm-cross-x64": ("arm64", "x64"),
    "x64-emulated": ("x64", "x64"),
    "x64-native": ("x64", "x64"),
}


def pe_machine(path):
    with Path(path).open("rb") as binary:
        if binary.read(2) != b"MZ":
            raise ValueError("invalid-dos-signature")
        binary.seek(0x3C)
        offset = struct.unpack("<I", binary.read(4))[0]
        binary.seek(offset)
        if binary.read(4) != b"PE\0\0":
            raise ValueError("invalid-pe-signature")
        return struct.unpack("<H", binary.read(2))[0]


def checked(command, *, cwd=None, env=None):
    return subprocess.check_output(
        [str(part) for part in command], cwd=cwd, env=env, text=True,
        encoding="utf-8", errors="replace",
    ).strip()


def measure(command, *, cwd, env, output, label, metadata):
    stdout = output / f"{label}.stdout.log"
    stderr = output / f"{label}.stderr.log"
    with stdout.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
        start = time.perf_counter()
        result = subprocess.run(
            [str(part) for part in command], cwd=cwd, env=env, stdout=out, stderr=err,
        )
        seconds = time.perf_counter() - start
    record = {
        **metadata, "phase": label, "seconds": seconds, "exit_code": result.returncode,
        "command": [str(part) for part in command],
    }
    with (output / "measurements.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
    print(json.dumps(record), flush=True)
    if result.returncode:
        print(stderr.read_text(encoding="utf-8"), flush=True)
        print(stdout.read_text(encoding="utf-8"), flush=True)
        raise subprocess.CalledProcessError(result.returncode, command)
    return stdout


def cargo_artifacts(log, target_arch, *, tests=False):
    artifacts = []
    compiled = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        message = json.loads(line)
        if message.get("reason") != "compiler-artifact":
            continue
        compiled += not message["fresh"]
        if tests and message.get("executable") and message["profile"]["test"]:
            executable = Path(message["executable"])
            if pe_machine(executable) != MACHINES[target_arch]:
                raise ValueError("unexpected-test-architecture")
            artifacts.append((executable, Path(message["manifest_path"]).parent))
    if not compiled:
        raise ValueError("no-compilation-observed")
    if tests and not artifacts:
        raise ValueError("no-test-executables")
    return sorted(set(artifacts))


def test_counts(log):
    matches = re.findall(
        r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;",
        log.read_text(encoding="utf-8"),
    )
    if not matches or any(int(failed) for _, failed, _ in matches):
        raise ValueError("invalid-test-result")
    return {
        "passed": sum(int(passed) for passed, _, _ in matches),
        "ignored": sum(int(ignored) for _, _, ignored in matches),
    }


def case_order(host, replicate):
    if host == "x64":
        return ["x64-native"]
    cases = ["arm-native", "arm-cross-x64", "x64-emulated"]
    rotation = (replicate - 1) % len(cases)
    return cases[rotation:] + cases[:rotation]


def vs_environment(host, target):
    locator = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    installation = checked([
        locator, "-latest", "-products", "*", "-property", "installationPath",
    ])
    setup = Path(installation) / "VC/Auxiliary/Build/vcvarsall.bat"
    if not installation or not setup.is_file():
        raise FileNotFoundError("vcvarsall")
    names = {"arm64": "arm64", "x64": "amd64"}
    architecture = names[host] if host == target else f"{names[host]}_{names[target]}"
    command = f'call "{setup}" {architecture} >nul && set'
    environment = dict(os.environ)
    environment["PATH"] = str(locator.parent) + os.pathsep + environment["PATH"]
    configured = subprocess.check_output(
        f'"{os.environ["ComSpec"]}" /d /s /c "{command}"',
        env=environment, text=True, encoding="utf-8", errors="replace",
    )
    for line in configured.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            environment[key.upper()] = value
    return environment


def inventory(host, output):
    data = json.loads(checked([
        "pwsh", "-NoProfile", "-Command",
        "@{cpu=@(Get-CimInstance Win32_Processor | "
        "Select-Object Name,Architecture,NumberOfCores,NumberOfLogicalProcessors);"
        "memory=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory;"
        "os=(Get-CimInstance Win32_OperatingSystem).Caption;"
        "build=[Environment]::OSVersion.Version.ToString()} | ConvertTo-Json -Depth 5",
    ]))
    expected = 12 if host == "arm64" else 9
    if not data["cpu"] or any(cpu["Architecture"] != expected for cpu in data["cpu"]):
        raise ValueError("unexpected-runner-architecture")
    data.update({
        "image": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion"),
        "runner": os.environ.get("RUNNER_NAME"), "runner_arch": os.environ.get("RUNNER_ARCH"),
        "experiment_sha": os.environ.get("GITHUB_SHA"), "rust_version": VERSION,
        "source_sha": SOURCE, "python_version": os.sys.version,
        "recorded_at": time.time(),
    })
    (output / "inventory.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def run_case(case, args):
    compiler_host, target = CASES[case]
    triple = TRIPLES[target]
    toolchain = f"{VERSION}-{TRIPLES[compiler_host]}"
    subprocess.run([
        "rustup", "toolchain", "install", toolchain, "--profile", "minimal", "--no-self-update",
    ], check=True)
    subprocess.run(["rustup", "target", "add", "--toolchain", toolchain, triple], check=True)
    compiler = Path(checked(["rustup", "which", "--toolchain", toolchain, "rustc"]))
    cargo = Path(checked(["rustup", "which", "--toolchain", toolchain, "cargo"]))
    for executable in [compiler, cargo]:
        if pe_machine(executable) != MACHINES[compiler_host]:
            raise ValueError("unexpected-toolchain-architecture")
    rust_version = checked([compiler, "-vV"])
    if f"host: {TRIPLES[compiler_host]}" not in rust_version:
        raise ValueError("unexpected-toolchain-host")
    environment = vs_environment(args.host, target)
    environment.update({
        "RUSTC": str(compiler), "RUSTUP_TOOLCHAIN": toolchain,
        "CARGO_INCREMENTAL": "0", "CARGO_BUILD_JOBS": "4",
        "RUSTC_WRAPPER": "", "RUSTC_WORKSPACE_WRAPPER": "",
        "RUSTFLAGS": "", "CARGO_ENCODED_RUSTFLAGS": "",
        "RUSTDOCFLAGS": "", "CARGO_ENCODED_RUSTDOCFLAGS": "",
    })
    environment["PATH"] = str(compiler.parent) + os.pathsep + environment["PATH"]
    linker = Path(shutil.which("link.exe", path=environment["PATH"]) or "")
    if not linker.is_file() or pe_machine(linker) != MACHINES[args.host]:
        raise ValueError("unexpected-linker-architecture")
    environment[f"CARGO_TARGET_{triple.upper().replace('-', '_')}_LINKER"] = str(linker)
    target_dir = args.work / case
    if target_dir.exists():
        raise FileExistsError(target_dir)
    output = args.output / case
    output.mkdir()
    metadata = {
        "case": case, "replicate": args.replicate, "host": args.host,
        "compiler_host": compiler_host, "target": target, "jobs": 4,
    }
    (output / "toolchain.json").write_text(json.dumps({
        **metadata, "rustc": rust_version, "cargo": checked([cargo, "-V"]),
        "linker": str(linker), "linker_sha256": hashlib.sha256(linker.read_bytes()).hexdigest(),
        "vc_tools_version": environment.get("VCTOOLSVERSION"),
        "windows_sdk_version": environment.get("WINDOWSSDKVERSION"),
    }, indent=2), encoding="utf-8")
    subprocess.run(
        [cargo, "fetch", "--locked", "--target", triple],
        cwd=args.source, env=environment, check=True,
    )
    common = [
        "--workspace", "--exclude", "grep-pcre2",
        "--locked", "--offline", "--release", "--target", triple,
        "--target-dir", target_dir, "--jobs", "4", "--message-format", "json",
    ]
    build = measure(
        [cargo, "build", *common], cwd=args.source, env=environment, output=output,
        label="clean-release-build", metadata=metadata,
    )
    cargo_artifacts(build, target)
    compiled_tests = measure(
        [cargo, "test", "--no-run", *common], cwd=args.source, env=environment,
        output=output, label="test-compilation-after-release-build", metadata=metadata,
    )
    executables = cargo_artifacts(compiled_tests, target, tests=True)
    results = []
    for iteration in range(1, 4):
        total = {"passed": 0, "ignored": 0}
        for index, (executable, working_directory) in enumerate(executables):
            log = measure(
                [executable, "--test-threads=4"], cwd=working_directory, env=environment,
                output=output, label=f"test-{iteration}-{index}", metadata={
                    **metadata, "iteration": iteration,
                    "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                },
            )
            for key, value in test_counts(log).items():
                total[key] += value
        if not total["passed"]:
            raise ValueError("no-tests-passed")
        results.append(total)
    if any(result != results[0] for result in results):
        raise ValueError("inconsistent-test-counts")
    (output / "tests.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    subprocess.run(
        [cargo, "clean", "--target-dir", target_dir], cwd=args.source, env=environment, check=True,
    )
    return results[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=["arm64", "x64"], required=True)
    parser.add_argument("--replicate", type=int, choices=[1, 2, 3], required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.source = args.source.resolve()
    args.work = args.work.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True)
    if checked(["git", "rev-parse", "HEAD"], cwd=args.source) != SOURCE:
        raise ValueError("unexpected-source-revision")
    lockfile = args.source / "Cargo.lock"
    original_lock = lockfile.read_bytes()
    inventory(args.host, args.output)
    order = case_order(args.host, args.replicate)
    (args.output / "order.json").write_text(json.dumps(order), encoding="utf-8")
    test_results = {}
    for case in order:
        test_results[case] = run_case(case, args)
        if lockfile.read_bytes() != original_lock:
            raise ValueError("lockfile-modified")
    if args.host == "arm64" and test_results["arm-cross-x64"] != test_results["x64-emulated"]:
        raise ValueError("inconsistent-x64-test-coverage")


if __name__ == "__main__":
    main()
