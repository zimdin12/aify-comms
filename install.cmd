@echo off
rem Run install.sh from PowerShell or cmd. A bare `bash` there is WSL's, which installs for Linux inside WSL instead
rem of for this Windows host, so this finds Git Bash and runs install.sh with it. Arguments pass through unchanged.
setlocal
set "GITBASH="
for %%B in ("%ProgramFiles%\Git\bin\bash.exe" "%ProgramW6432%\Git\bin\bash.exe" "%LOCALAPPDATA%\Programs\Git\bin\bash.exe") do if not defined GITBASH if exist "%%~B" set "GITBASH=%%~B"
if not defined GITBASH for /f "delims=" %%G in ('where git 2^>nul') do if not defined GITBASH if exist "%%~dpG..\bin\bash.exe" set "GITBASH=%%~dpG..\bin\bash.exe"
if not defined GITBASH (
  echo install.cmd: Git Bash was not found. Install Git for Windows, then run this again. 1>&2
  exit /b 69
)
"%GITBASH%" "%~dp0install.sh" %*
exit /b %ERRORLEVEL%
