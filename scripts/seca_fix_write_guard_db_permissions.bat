@echo off
REM SECA F348 (2026-11-07): write_guard.db (and its data\ folder) should not
REM be world-readable. Restricts access to the current user + Administrators
REM only, removing inherited broader permissions (e.g. Users/Everyone).
REM This changes OS-level file permissions — not run automatically by Claude;
REM run this once yourself from an elevated (Administrator) command prompt.

set DB_DIR=%~dp0..\data

echo Locking down permissions on %DB_DIR% ...
icacls "%DB_DIR%" /inheritance:r
icacls "%DB_DIR%" /grant:r "%USERNAME%":(OI)(CI)F
icacls "%DB_DIR%" /grant:r "Administrators":(OI)(CI)F

echo.
echo Current permissions:
icacls "%DB_DIR%"

echo.
echo Done. Re-run this after any Windows reinstall of write-guard's data folder,
echo or if a new db file ever reappears with default (inherited) permissions.
pause
