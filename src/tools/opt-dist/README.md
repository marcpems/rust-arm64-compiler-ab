# Optimized build pipeline
This binary implements a heavily optimized build pipeline for `rustc` and `LLVM` artifacts that are used for both for
benchmarking using the perf. bot and for final distribution to users.

It uses LTO, PGO and BOLT to optimize the compiler and LLVM as much as possible.
This logic is not part of bootstrap, because it needs to invoke bootstrap multiple times, force-rebuild various
artifacts repeatedly and sometimes go around bootstrap's cache mechanism.

The experimental Windows MSVC distribution jobs use fresh rustc and LLVM PGO
profiles and Rust-side ThinLTO on both x64 and Arm64. LLVM-side ThinLTO is disabled
so native objects can be linked by Microsoft's `link.exe`, with no replacement
of the existing compiler or librarian. Static LLVM requires relinking
the training compiler after instrumentation; shared LLVM keeps the existing
compiler. Windows Arm64 does not train the unsupported Cranelift backend.

The experiment explicitly pins the MSVC linker and librarian in both bootstrap
and CMake; relying on CMake's defaults can select the LLVM tools installed beside
clang-cl. The Arm64 opt-dist helper is built only for the native host, while the final distribution
retains Arm64EC, full tools and the extracted-distribution tests.

## Building on Windows

Use a native MSVC developer shell with Rust's usual build prerequisites and the
native LLVM version from `src/ci/scripts/install-clang.sh`, extracted to
`citools\clang-rust`. Use a private Clang installation, not a shared system
installation: preparation replaces its profiling library. Start with initialized
submodules, without an existing `bootstrap.toml` or cached compiler/CMake builds.

### Required profiling-runtime preparation

The Arm64 experiment found two independent compiler-rt problems: padding after
the Windows names sentinel, and omitted bitmap padding when online merging
profiles. The experimental patch in `windows-msvc-profile-runtime.patch` fixes
both. Rust's in-tree compiler-rt supplies the rustc profiling runtime; Clang's
separate installation supplies the LLVM profiling runtime. Patching only one
does not repair the other. Apply the same patch to both exact revisions below.
It is an experimental patch, not a claim of general profiling-mode qualification.

The bootstrap runtime search-path correction is already included in this Rust
branch. It quotes and normalizes the `-L` argument consumed by `rustc_llvm`'s
shell-word parser, preventing Windows backslashes and spaces from corrupting
the selected runtime directory.

Run the following from the Rust checkout root in PowerShell; it clones the
separate LLVM runtime source into a sibling directory. These commands require fresh, unpatched
LLVM checkouts and fail rather than silently accepting other revisions:

```powershell
$ErrorActionPreference = "Stop"
# Use x86_64-pc-windows-msvc on an x64 host.
$triple = "aarch64-pc-windows-msvc"
$llvm = (Resolve-Path "citools\clang-rust\bin").Path.Replace('\', '/')
$patch = (Resolve-Path "src\tools\opt-dist\windows-msvc-profile-runtime.patch").Path
$inTree = (Resolve-Path "src\llvm-project").Path
git clone --depth 1 --branch llvmorg-22.1.8 https://github.com/llvm/llvm-project.git ..\llvm-runtime-22
if ($LASTEXITCODE) { throw "LLVM runtime source clone failed" }
$runtimeSource = (Resolve-Path "..\llvm-runtime-22").Path
foreach ($source in @(
    @($inTree, "aab11ee0ea5ea9a1d462ad0bc1fae9d9bb643019"),
    @($runtimeSource, "ca7933e47d3a3451d81e72ac174dcb5aa28b59d1")
)) {
    $revision = git -C $source[0] rev-parse HEAD
    if ($LASTEXITCODE -or $revision -ne $source[1]) { throw "Unexpected LLVM source revision" }
    git -C $source[0] apply --check $patch
    if ($LASTEXITCODE) { throw "Runtime patch check failed" }
    git -C $source[0] apply $patch
    if ($LASTEXITCODE) { throw "Runtime patch failed" }
}

$linker = (Get-Command link.exe).Source.Replace('\', '/')
$librarian = (Get-Command lib.exe).Source.Replace('\', '/')
if ($linker -notmatch "Microsoft Visual Studio" -or $librarian -notmatch "Microsoft Visual Studio") {
    throw "Use the native Microsoft Visual Studio developer shell"
}
New-Item -ItemType Directory build\msvc-pgo-preparation | Out-Null
$preparation = (Resolve-Path "build\msvc-pgo-preparation").Path
$toolchain = Join-Path $preparation "native-tools.cmake"
@"
set(CMAKE_LINKER "$linker" CACHE FILEPATH "" FORCE)
set(CMAKE_AR "$librarian" CACHE FILEPATH "" FORCE)
"@ | Set-Content -Encoding utf8 $toolchain
$env:CMAKE_TOOLCHAIN_FILE = $toolchain
$env:PATH = "$llvm;$env:PATH"
$runtimeBuild = Join-Path $preparation "clang-runtime-build"
cmake -S "$runtimeSource\compiler-rt" -B $runtimeBuild -G Ninja `
    "-DCMAKE_C_COMPILER=$llvm/clang-cl.exe" "-DCMAKE_CXX_COMPILER=$llvm/clang-cl.exe" `
    "-DCMAKE_C_COMPILER_TARGET=$triple" "-DCMAKE_CXX_COMPILER_TARGET=$triple" `
    -DCMAKE_BUILD_TYPE=Release -DCOMPILER_RT_DEFAULT_TARGET_ONLY=ON `
    -DCOMPILER_RT_BUILD_PROFILE=ON -DCOMPILER_RT_INCLUDE_TESTS=OFF `
    -DCOMPILER_RT_BUILD_BUILTINS=OFF -DCOMPILER_RT_BUILD_SANITIZERS=OFF `
    -DCOMPILER_RT_BUILD_XRAY=OFF -DCOMPILER_RT_BUILD_LIBFUZZER=OFF `
    -DCOMPILER_RT_BUILD_CTX_PROFILE=OFF -DCOMPILER_RT_BUILD_MEMPROF=OFF `
    -DCOMPILER_RT_BUILD_ORC=OFF
if ($LASTEXITCODE) { throw "Profiling runtime configuration failed" }
cmake --build $runtimeBuild --target profile --verbose
if ($LASTEXITCODE) { throw "Profiling runtime build failed" }
$resource = & "$llvm/clang-cl.exe" --print-resource-dir
if ($LASTEXITCODE) { throw "Clang resource lookup failed" }
$suffix = if ($triple -eq "aarch64-pc-windows-msvc") { "aarch64" } else { "x86_64" }
$library = "clang_rt.profile-$suffix.lib"
Copy-Item "$resource\lib\windows\$library" "$preparation\original-$library"
Copy-Item "$runtimeBuild\lib\windows\$library" "$resource\lib\windows\$library"
```

Keep `CMAKE_TOOLCHAIN_FILE` set for every subsequent bootstrap invocation,
including the shipped rust-lld build. That shipped tool supports WASM; it does
not replace the native compiler linker. The compiler-only harness additionally
checks actual CMake caches, exact repeated-profile counts, and (for the repaired
Arm64 backend compiler) PDB runtime-object provenance. A successful library
build alone is not equivalent to those checks.

### Experimental distribution command

Continue in the same shell after preparation:

```powershell
$targets = $triple
$backends = "llvm"
$extra = @()
if ($triple -eq "aarch64-pc-windows-msvc") {
    $targets += ",arm64ec-pc-windows-msvc"
} else {
    $backends += ",cranelift"
    $extra = @("--set", "rust.codegen-units=1")
}
python src\bootstrap\configure.py --build=$triple --host=$triple --target=$targets `
   --enable-full-tools --enable-profiler --set rust.codegen-backends=$backends `
   --set llvm.download-ci-llvm=false --set rust.download-rustc=false `
   --set llvm.thin-lto=false --set llvm.link-shared=false --set rust.lto=thin `
   --set "target.$triple.linker=link.exe" --set "target.$triple.ar=$librarian" `
   --set "llvm.clang-cl=$llvm/clang-cl.exe" @extra
if ($LASTEXITCODE) { throw "configure failed" }
python x.py build --host $triple --target $triple --set rust.debug=true opt-dist
if ($LASTEXITCODE) { throw "opt-dist build failed" }
$env:PGO_HOST = $triple
& ".\build\$triple\stage1-tools-bin\opt-dist.exe" windows-ci -- `
    python x.py dist bootstrap --include-default-paths
if ($LASTEXITCODE) { throw "optimized distribution failed" }
```

These settings are not a qualified release pipeline. The separate compiler-only
IronRDP experiment compares no PGO, PGO, and PGO plus Rust-side ThinLTO with the
same native tools and byte-identical training profiles for the two PGO variants.
It does not qualify the distribution commands above or the six-hour CI budget.
The distribution job definitions do not yet automate the runtime preparation
above. Use the pinned compiler-only harness below for the exercised CI path;
do not treat the distribution jobs as a ready-to-deploy replacement.

### Measured source and evidence

The compiler-only measurements use Rust commit
`5b615ab3eb2cd5a26afc6025b5863180ffdc20d7` plus recorded source patches, not this
later consolidated branch tip. The runtime patch is identical to the one used
in those builds. The integrated bootstrap path correction is identical to the
Arm64 fix; the original x64 backend training predates that correction and the
PDB audit. Its recorded standalone Clang runtime hash does not prove which
profiling library was linked into that training compiler.

The [pinned experiment harness](https://github.com/marcpems/IronRDP-ci-benchmark/tree/25567914accb73100822794c971b3a6661ae6fa8)
contains the compiler-only configuration, explicit native-tool checks, runtime
probes, training scripts, and
[diagnostic history](https://github.com/marcpems/IronRDP-ci-benchmark/blob/25567914accb73100822794c971b3a6661ae6fa8/ci-benchmark/msvc-linker/RUNTIME-DIAGNOSTICS.md).
Successful final compiler construction is recorded separately for
[x64](https://github.com/marcpems/IronRDP-ci-benchmark/actions/runs/38021536352)
and [Arm64](https://github.com/marcpems/IronRDP-ci-benchmark/actions/runs/38056115559).
All three variants were built for each architecture; these are construction
results, not a claim that the full IronRDP performance study or release
qualification has completed.

MSVC `/LTCG` is not a replacement for LLVM ThinLTO: it consumes MSVC `/GL`
objects, not LLVM bitcode. Building LLVM with `cl.exe` and MSVC PGO/LTCG would
change the C++ compiler and profiling system, outside this same-tools experiment.
