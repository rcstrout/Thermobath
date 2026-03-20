# Agent Build Instructions

## Rebuild The Windows Executable

Use the project build script instead of ad hoc PyInstaller commands.

- Fast rebuild (default):
  - `powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1`
- Clean rebuild:
  - `powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -Clean`

## What The Script Handles

- Uses `Start-Process -Wait -PassThru` for reliable exit codes.
- Writes logs to:
  - `build/pyinstaller_stdout.log`
  - `build/pyinstaller_stderr.log`
  - `build/rebuild_exitcode.txt`
- Detects `WinError 32` lock conflicts and retries once after clearing `build/ThermobathController`.

## Prompt Patterns For Agents

- "Run a fast executable rebuild using scripts/build_exe.ps1"
- "Run a clean executable rebuild using scripts/build_exe.ps1 -Clean"
- "If build fails, inspect build/pyinstaller_stderr.log and report the root cause"
