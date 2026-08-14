$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'dsdevice_bridge.c'
$buildDirectory = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Force -Path $buildDirectory | Out-Null
$output = Join-Path $buildDirectory 'DSDEVICE.DLL'
$testSource = Join-Path $PSScriptRoot 'bridge_smoke_test.c'
$testOutput = Join-Path $buildDirectory 'bridge_smoke_test.exe'
$compilerArgs = @(
    '-shared'
    '-O2'
    '-Wall'
    '-Wextra'
    '-Wl,--kill-at'
    '-static-libgcc'
    '-o'
    $output
    $source
)
& i686-w64-mingw32-gcc.exe @compilerArgs
if ($LASTEXITCODE -ne 0) { throw "bridge build failed: $LASTEXITCODE" }
& i686-w64-mingw32-gcc.exe -O2 -Wall -Wextra -static-libgcc -o $testOutput $testSource
if ($LASTEXITCODE -ne 0) { throw "smoke test build failed: $LASTEXITCODE" }
Get-Item -LiteralPath $output | Select-Object FullName, Length, LastWriteTime
& llvm-readobj --file-headers --coff-exports $output
