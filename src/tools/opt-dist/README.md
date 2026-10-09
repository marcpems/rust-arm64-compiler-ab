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

Both jobs retain the default MSVC linker and librarian. The Arm64 opt-dist
helper is built only for the native host, while the final distribution
retains Arm64EC, full tools and the extracted-distribution tests.

## Building on Windows

Use a native MSVC developer shell with Rust's usual build prerequisites and the
native LLVM version from `src/ci/scripts/install-clang.sh`, extracted to
`citools\clang-rust`. Start without an existing `bootstrap.toml`.
From the checkout root in PowerShell:

```powershell
# Use x86_64-pc-windows-msvc on an x64 host.
$triple = "aarch64-pc-windows-msvc"
$targets = $triple
$backends = "llvm"
$extra = @()
if ($triple -eq "aarch64-pc-windows-msvc") {
    $targets += ",arm64ec-pc-windows-msvc"
} else {
    $backends += ",cranelift"
    $extra = @("--set", "rust.codegen-units=1")
}
$llvm = (Resolve-Path "citools\clang-rust\bin").Path.Replace('\', '/')
$env:PATH = "$llvm;$env:PATH"
python src\bootstrap\configure.py --build=$triple --host=$triple --target=$targets `
    --enable-full-tools --enable-profiler --set rust.codegen-backends=$backends `
    --set llvm.download-ci-llvm=false --set rust.download-rustc=false `
    --set llvm.thin-lto=false --set llvm.link-shared=false --set rust.lto=thin `
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

MSVC `/LTCG` is not a replacement for LLVM ThinLTO: it consumes MSVC `/GL`
objects, not LLVM bitcode. Building LLVM with `cl.exe` and MSVC PGO/LTCG would
change the C++ compiler and profiling system, outside this same-tools experiment.
