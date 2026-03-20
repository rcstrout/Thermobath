param(
    [switch]$Clean,
    [string]$PythonExe = "python",
    [string]$SpecPath = "ThermobathController.spec"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$buildDir = Join-Path $projectRoot "build"
$distExe = Join-Path $projectRoot "dist\ThermobathController.exe"
$stdoutLog = Join-Path $buildDir "pyinstaller_stdout.log"
$stderrLog = Join-Path $buildDir "pyinstaller_stderr.log"
$exitCodeFile = Join-Path $buildDir "rebuild_exitcode.txt"

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null

# Resolve python executable if the default command is not available.
if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
    if (Get-Command "python3.13.exe" -ErrorAction SilentlyContinue) {
        $PythonExe = "python3.13.exe"
    }
    elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
        $PythonExe = "py"
    }
    else {
        throw "Could not find a Python executable. Pass -PythonExe explicitly."
    }
}

function Invoke-PyInstallerBuild {
    param(
        [bool]$UseClean
    )

    Remove-Item $stdoutLog, $stderrLog, $exitCodeFile -ErrorAction SilentlyContinue

    $args = @("-m", "PyInstaller")
    if ($UseClean) {
        $args += "--clean"
    }
    $args += $SpecPath

    Write-Host "Running: $PythonExe $($args -join ' ')"

    $proc = Start-Process -FilePath $PythonExe `
        -ArgumentList $args `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog

    $proc.ExitCode | Out-File $exitCodeFile -Encoding ascii
    return $proc.ExitCode
}

function Test-LockErrorInLog {
    if (-not (Test-Path $stderrLog)) {
        return $false
    }

    $logText = Get-Content $stderrLog -Raw
    return ($logText -match "WinError 32")
}

$useClean = [bool]$Clean
$modeLabel = if ($useClean) { "clean" } else { "fast" }
Write-Host "Build mode: $modeLabel"

$exitCode = Invoke-PyInstallerBuild -UseClean:$useClean

if ($exitCode -ne 0 -and (Test-LockErrorInLog)) {
    Write-Host "Detected WinError 32 lock during build. Cleaning build artifacts and retrying once..."
    Remove-Item (Join-Path $buildDir "ThermobathController") -Recurse -Force -ErrorAction SilentlyContinue

    $exitCode = Invoke-PyInstallerBuild -UseClean:$useClean
}

if ($exitCode -ne 0) {
    Write-Host "Build failed with exit code $exitCode"
    if (Test-Path $stderrLog) {
        Write-Host "--- stderr tail ---"
        Get-Content $stderrLog -Tail 40
    }
    exit $exitCode
}

if (-not (Test-Path $distExe)) {
    Write-Host "Build finished but executable was not found at $distExe"
    exit 2
}

$exeInfo = Get-Item $distExe
Write-Host "Build succeeded"
Write-Host "Executable: $($exeInfo.FullName)"
Write-Host "Size: $($exeInfo.Length) bytes"
Write-Host "LastWriteTime: $($exeInfo.LastWriteTime)"
exit 0
