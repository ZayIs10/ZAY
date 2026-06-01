# System Fixes — Stop GPU/RAM Freezes

Utilities to prevent Windows freezing when HyperFrames renders run alongside Cursor + Claude Code on this Lenovo ThinkPad (32 GB RAM, GTX 1050 Ti Max-Q 4 GB VRAM).

## 1. fix_gpu_tdr.reg — apply once, then reboot

Increases the GPU driver timeout from 2s to 60s. When VRAM spills under load, the driver gets time to recover instead of triggering a multi-minute desktop freeze.

**Apply:**
1. Double-click `fix_gpu_tdr.reg`
2. Accept the UAC prompt → Yes → OK
3. **Reboot** (required — value is read at boot)

**Verify after reboot:**
```powershell
Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" -Name TdrDelay, TdrDdiDelay
```
Should show `TdrDelay : 60` and `TdrDdiDelay : 60`.

**Revert:** open regedit → navigate to `HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers` → delete `TdrDelay` and `TdrDdiDelay` → reboot.

## 2. set_render_priority.ps1 — keeps Cursor responsive during renders

Background watcher that drops HyperFrames/ffmpeg to **BelowNormal** priority, so Windows gives Cursor/Claude Code the CPU first when there's contention.

**Run manually:**
```powershell
pwsh -File .\set_render_priority.ps1
```
Leave the window open while you work. Ctrl+C to stop.

**Run at every login (recommended):**
1. Open **Task Scheduler** (Win+R → `taskschd.msc`)
2. Action → **Create Basic Task** → Name: `Render Priority Watcher`
3. Trigger: **When I log on**
4. Action: **Start a program**
   - Program: `pwsh.exe`
   - Arguments: `-WindowStyle Hidden -File "C:\Users\Marc\Desktop\Gen Z autamation\scripts\system_fixes\set_render_priority.ps1"`
5. Finish → right-click the task → Properties → check **Run with highest privileges**

**Find the right process name first:**
The script defaults to watching for `HyperFrames`, `hyperframes`, `ffmpeg`. Open Task Manager → Details tab while a render is running, find the actual process name, and add it to `$renderProcessNames` in the script if it's different.

## 3. Other free wins (manual, do once)

- **Settings → Accessibility → Visual effects → Transparency effects: OFF** (frees ~300 MB VRAM)
- **Task Manager → Startup tab** → disable OneDrive, Teams, Spotify, Discord, Slack
- **NVIDIA Control Panel → Manage 3D settings → Program Settings → HyperFrames** → Power management mode: **Prefer maximum performance**
- **services.msc → Windows Search → Stop, set to Manual** (stops disk thrashing during renders)
- In **Cursor**: Ctrl+Shift+P → "Preferences: Configure Runtime Arguments" → add `"disable-hardware-acceleration": true` while rendering

## 4. Workflow rule (the biggest win, $0)

**Don't run HyperFrames renders and Claude Code at the same time.** Queue renders for overnight or while you're AFK. The freezes mostly disappear with this single habit change.
