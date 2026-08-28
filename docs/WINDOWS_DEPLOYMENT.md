# Windows Deployment — hosting the always-on watcher

Purpose: run `watch.py` on a machine that stays on, so anyone pasting a URL
into the shared Google Sheet gets it processed within ~10 seconds, regardless
of whether your laptop is open.

> **Tested on macOS, not on Windows.** The code paths are Windows-aware
> (`_runtime.py` resolves `.venv\Scripts\yt-dlp.exe`, the one POSIX call is
> guarded), but this checklist has not been executed end to end on Windows.
> Expect one or two small surprises; the troubleshooting table covers the
> likely ones.

Budget about 30 minutes.

---

## Phase 1 — Prerequisites

### ☐ 1.1 Python 3.11

Download from [python.org/downloads](https://www.python.org/downloads/).

**Tick "Add python.exe to PATH" on the first installer screen.** It is off by
default and skipping it causes most of the errors below.

Verify in a **new** PowerShell window:
```powershell
py -3.11 --version
```

### ☐ 1.2 ffmpeg

```powershell
winget install Gyan.FFmpeg
```

Then **close and reopen PowerShell** — PATH changes do not apply to an
already-open window. Verify:
```powershell
ffmpeg -version
```

If `winget` is unavailable, use `choco install ffmpeg`, or download from
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add the `bin` folder to
PATH manually.

### ☐ 1.3 Stop the machine sleeping

**This is the step people skip, and it silently breaks everything.** A PC
that sleeps is not an always-on host — the watcher stops and the sheet goes
quiet with no error anywhere.

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 15
```

The monitor can still sleep — that costs nothing. The *machine* must not.

Also consider **Settings → Windows Update → Advanced → Active hours** so a
forced restart does not land mid-demo.

---

## Phase 2 — Get the code and credentials across

### ☐ 2.1 Clone or copy

```powershell
cd C:\
git clone <your-repo-url> video-pipeline
cd video-pipeline\video-pipeline
```

No git on the machine? Copy the folder over, but **exclude `.venv`** — a
virtualenv contains absolute paths and will not work on another machine. It
gets rebuilt in the next step.

### ☐ 2.2 Credentials

Two files must reach the Windows machine. Both are gitignored, so they will
**not** arrive with a `git clone`. Copy them manually:

| File | What it is |
|---|---|
| `.env` | Your MiniMax key and pipeline settings |
| `inputs\client_secret.json` | Your Google OAuth client |

**Do not copy `inputs\google_token.json`.** It would work, but you will
generate a fresh one on this machine in Phase 4 — and copying live tokens
between machines is a habit worth not forming.

Transfer them the way you would any secret. Not email, not a public share.

---

## Phase 3 — Install

### ☐ 3.1 Run setup

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

The `-ExecutionPolicy Bypass` is needed because Windows blocks unsigned
scripts by default. It applies to this one invocation only and changes
nothing permanently.

### ☐ 3.2 Verify — no credentials required

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Expect **156 passed**. This proves the install works before any credential is
involved. If this fails, stop and fix it here — nothing later will work.

### ☐ 3.3 Verify the pipeline itself

```powershell
.\.venv\Scripts\python.exe run.py --max 2 --publisher local --brand generic
```

Expect `Published: 2`. Output in `outputs\published\`. This confirms yt-dlp,
ffmpeg and the LLM key all work on this machine.

---

## Phase 4 — Google consent

### ☐ 4.1 Authorise on this machine

```powershell
.\.venv\Scripts\python.exe run.py --max 1 --publisher gdrive
```

A browser opens. Sign in with the **same Google account** that owns the
sheet, click through "Google hasn't verified this app" (**Advanced → Go to…**),
and approve Drive + Sheets.

This writes `inputs\google_token.json`. It is machine-local — the Mac keeps
its own.

### ☐ 4.2 Confirm it reached Drive

Check your Drive for `VideoPipeline\generic\<today's date>\`. A video should
be there with a readable filename.

---

## Phase 5 — Run the watcher

### ☐ 5.1 Start it

```powershell
.\.venv\Scripts\python.exe watch.py --sheet <SHEET_ID> --publisher gdrive
```

Leave this window open. Paste a URL into column A of the sheet and watch:

```
15:49:17 detected 1 new URL(s) - row 8
15:49:17 running pipeline...
15:50:33 waiting for the next URL (1 processed this session)
```

### ☐ 5.2 Make it survive a reboot

A terminal window works but dies on logout, reboot or an accidental close.
Pick one:

**Option A — Task Scheduler** *(recommended: no extra software)*

1. Open **Task Scheduler** → **Create Task** (not *Basic* Task)
2. **General:** name it `VideoPipelineWatcher`. Select **"Run whether user is
   logged on or not"** and **"Run with highest privileges"**
3. **Triggers:** New → **At startup**. Tick **"Repeat task every 5 minutes"**
   for an indefinite duration — this restarts it if it ever exits
4. **Actions:** New → Start a program
   - Program: `C:\video-pipeline\video-pipeline\.venv\Scripts\python.exe`
   - Arguments: `watch.py --sheet <SHEET_ID> --publisher gdrive`
   - **Start in:** `C:\video-pipeline\video-pipeline`
5. **Conditions:** untick **"Start the task only if the computer is on AC
   power"** if it is a laptop
6. **Settings:** tick **"If the task fails, restart every 1 minute"**

**"Start in" is mandatory** — without it the script cannot find `.env`,
`configs\` or `inputs\`, and fails immediately with confusing errors.

**Option B — a visible window**

Simplest, and you can see the log. Dies on logout. Fine while you are actively
demoing:

```powershell
Start-Process powershell -ArgumentList '-NoExit','-Command','cd C:\video-pipeline\video-pipeline; .\.venv\Scripts\python.exe watch.py --sheet <SHEET_ID> --publisher gdrive'
```

### ☐ 5.3 Confirm it is actually alive

```powershell
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, StartTime
```

Better: paste a URL into the sheet and confirm the row fills in. That tests
the whole chain, not just the process.

---

## Phase 6 — Weekly upkeep

### ⚠️ The token expires every 7 days

Google expires OAuth tokens for apps in **Testing** mode after 7 days. When it
happens the watcher keeps running but every batch fails to publish.

**Symptom:** rows stop filling in; the log shows auth errors.

**Fix:** on the Windows machine, stop the watcher and run:
```powershell
.\.venv\Scripts\python.exe run.py --max 1 --publisher gdrive
```
Complete the browser consent, then restart the watcher.

**Do not** publish the app to Google to avoid this — the Sheets scope is
classed as sensitive and would trigger a verification review taking days.

Set a calendar reminder for every 6 days while the sheet is shared.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `py : The term 'py' is not recognized` | Python not on PATH. Reinstall ticking "Add to PATH", or use the full path to `python.exe`. |
| `ffmpeg not found` after installing | PATH not refreshed. Close and reopen PowerShell. |
| `cannot be loaded because running scripts is disabled` | Use `powershell -ExecutionPolicy Bypass -File setup.ps1` |
| Setup fails compiling a package | Install **Microsoft C++ Build Tools**, or use Python 3.11 rather than 3.12+ where more wheels are prebuilt. |
| Task Scheduler task "runs" but nothing happens | **Start in** is empty. It must be the project folder. |
| Watcher alive, rows never process | Wrong `SHEET_ID`, or the account that consented does not have edit access to that sheet. |
| Rows stop filling in after ~a week | Token expired. See Phase 6. |
| Everything held for review | Brand mode is on. Use `--brand generic` for unrestricted publishing. |
| Watcher stops overnight | The machine slept. Redo step 1.3. |

---

## Quick reference

```powershell
# Start the watcher
cd C:\video-pipeline\video-pipeline
.\.venv\Scripts\python.exe watch.py --sheet <SHEET_ID> --publisher gdrive

# Refresh Google auth (weekly)
.\.venv\Scripts\python.exe run.py --max 1 --publisher gdrive

# Health check
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe run.py --max 1 --publisher local --brand generic

# Stop everything
Get-Process python | Stop-Process
```
