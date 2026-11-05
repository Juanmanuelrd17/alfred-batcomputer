# Batcomputer (BCv1.2) - Engineering Rules & Failure Runbook

This document serves as the permanent institutional memory and preventative runbook for the Batcomputer HUD and voice companion on `jm-gaming-pc` (Arch Linux, Hyprland/Wayland, NVIDIA GTX 1080).

---

## 1. WebKitGTK / PipeWire Audio Contention (Root Cause of UI Freezes)

### Symptom:
Whenever the user clicks any button, tab, launcher, or interactive element, the entire desktop UI freezes for 2–5 seconds.

### Root Cause:
`alfred_daemon.py` continuously holds an active audio recording stream via `parec` (16kHz s16le PulseAudio capture) for voice recognition. In `static/index.html`, interactive UI elements called `playBeep()` which instantiated a WebAudio `AudioContext` and oscillator node on every user interaction. 

Under WebKitGTK with PipeWire-Pulse (`pipewire-pulse`), acquiring an audio output stream while a capture stream is active leads to mutex contention and thread stalls:
```text
pipewire-pulse: mod.protocol-pulse: [wayland_desktop_bg.py] timeout on stream 0x55ee... channel:0
```
This blocks WebKit's main rendering thread synchronously until the timeout expires.

### Strict Rule:
- **NEVER** use WebAudio oscillator synthesis or in-page audio element playback on click events in the WebKit layer-shell HUD.
- Keep `playBeep()` as an inert no-op function in `static/index.html`.
- If sound effects are needed in the future, trigger them asynchronously through the Python daemon using a non-blocking external utility (e.g. `pw-play` or `canberra-gtk-play`) or a dedicated isolated ALSA device.

---

## 2. NVIDIA Wayland File Descriptor Exhaustion (`anon_inode:sync_file`)

### Symptom:
The desktop HUD crashes after several minutes of usage or when launching applications, reporting `Too many open files` or silently disappearing.

### Root Cause:
WebKitGTK's DMA-BUF renderer leaks sync fence file descriptors (`anon_inode:sync_file`) under NVIDIA's proprietary driver on Wayland. Inspecting `/proc/<WebKitWebProcess_PID>/fd` revealed FD counts climbing from 27 to over 9,200+.

### Strict Rule:
- Always enforce `WEBKIT_DISABLE_DMABUF_RENDERER=1` in the environment before any WebKitGTK initialization:
  - In `launch-alfred.sh`: `export WEBKIT_DISABLE_DMABUF_RENDERER=1`
  - In `wayland_desktop_bg.py`: `os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"] = "1"`
- Periodically verify FD count with: `ls -1 /proc/$(pgrep -f WebKitWebProcess)/fd | wc -l` (must remain < 50).

---

## 3. DOM Layout Thrashing in Global Event Listeners

### Symptom:
Laggy mouse response, delayed hover states, or jank when scrolling.

### Root Cause:
Calling `window.getComputedStyle(element)` inside high-frequency global event listeners (`wheel`, `touchmove`, `mousemove`) forces the browser engine to perform synchronous recalculation of layout and styles (reflow).

### Strict Rule:
- **NEVER** traverse parent nodes calling `getComputedStyle()` inside global scroll or input handlers.
- Use selector-based matching:
  ```javascript
  const isScrollable = e.target.closest('#localFilesListContainer, .drawer-content, #cmdPaletteResults, .panel > div[style*="overflow"]');
  if (!isScrollable) {
      e.preventDefault();
  }
  ```

---

## 4. Hyprland Workspace Focusing & App Launching

### Symptom:
Clicking an app (e.g., Steam, VS Code, Browser) appears to do nothing or takes a very long time to open.

### Root Cause:
1. Steam runs in the background in `-silent` tray mode. Running just `steam` does not restore the main window.
2. In Hyprland, if an application window already exists on another workspace (e.g., Steam on Workspace 2 while the user is on Workspace 1), launching the command activates or unhides the window on Workspace 2 without switching the user's active monitor view. The user perceives this as a crash or freeze.

### Strict Rule:
- In `alfred_daemon.py` (`focus_hyprland_window`), query `hyprctl clients -j`. If the client exists:
  1. Determine its target workspace (`client["workspace"]["name"]` or `id`).
  2. Dispatch workspace switch: `hyprctl dispatch workspace <ws>`.
  3. Raise and focus the window: `hyprctl dispatch focuswindow address:<addr>`.
- For Steam, always invoke `steam steam://open/main` to unhide from system tray.
- Prefix all GUI application executions with `uwsm app --` for proper Wayland session cgroup scoping.
- In `static/index.html`, display instant feedback in the terminal (`⚡ Launching <APP>...`) before the HTTP fetch round-trip completes.

---

## 5. Viewport Layout & Overflow Boundaries

### Symptom:
The bottom row of the HUD (`.alfred-core` Batman logo or dock) is pushed off-screen, or the whole desktop allows vertical scrolling.

### Strict Rule:
- On 1080p (1920x1080), maintain strict height caps:
  - SVG Crest: `max-height: 180px;`
  - Root container: `overflow: hidden; height: 100vh;`
  - HUD grid: `padding-bottom: 14px;`
  - Dock / core margin: `margin-bottom: 6px;`

---

## 6. Zero JavaScript `alert()` Popups & Native Daemon Microphone Routing

### Symptom:
A blocking modal popup appears in the center of the desktop saying *"Microphone access requires HTTPS: The request is not allowed by the user agent..."* with a Close button, freezing interaction.

### Root Cause:
1. Web browsers enforce HTTPS / permission prompts for `navigator.mediaDevices.getUserMedia`. In a layer-shell desktop background loaded over `http://127.0.0.1:7778`, browser-level mic access fails if permissions aren't interactively confirmed.
2. The HUD called `alert(...)` inside the error catch block, which triggered a blocking GTK modal dialog across the entire screen.

### Strict Rule:
- **NEVER** use `alert()`, `confirm()`, or `prompt()` anywhere in the client code. All notices must be logged to the HUD terminal, notification toast, or an in-DOM modal.
- In `wayland_desktop_bg.py`, connect `script-dialog` on `WebKit2.WebView` to automatically call `dialog.confirm()` and dismiss all dialog attempts.
- When running on the desktop (`localBridgeOnline === true`), voice capture must **NOT** use browser `getUserMedia`. Clicking `MIC (MLX)` or pressing `Alt+Space` must signal the local Python daemon (`POST /local/talk`), which already possesses native PipeWire hardware audio capture privileges.

---

## 7. Mixed Refresh Rate (144Hz DP-1 + 60Hz DP-2) & Secondary Display Performance

### Symptom:
Desktop interactions (dragging windows, moving the cursor, typing, scrolling) feel smooth on the 144Hz monitor (`DP-1`), but feel abnormally laggy, stuttery, or choppy on the 60Hz monitor (`DP-2`).

### Root Causes:
1. **Unfocused Frame Throttling (`misc:render_unfocused_fps = 15`)**:
   Hyprland by default caps unfocused windows and monitors to 15 FPS. When the user interacts with or focuses windows on either monitor, the other monitor drops down to 15 FPS.
2. **Cursor Refresh Rate Throttling (`cursor:min_refresh_rate = 24`)**:
   Hyprland lowers cursor update frequency to 24 Hz during slow movements, causing noticeable judder on 60Hz displays.
3. **NVIDIA Hardware Cursor Plane Desync (`cursor:no_hardware_cursors = false`)**:
   On NVIDIA GPUs with mixed refresh rates, the hardware cursor DRM plane frequently desynchronizes with the 60Hz vblank interval, producing mouse jitter.
4. **Duplicate WebKit Background Surface on Secondary Screen**:
   `wayland_desktop_bg.py` was creating a layer-shell WebKit surface on both monitors. On DP-2 (where application windows like Antigravity IDE reside), running a second full-screen WebKit instance in software SHM mode forced continuous blitting and heavy CPU compositing.

### Strict Rules:
- In `wayland_desktop_bg.py`, only attach the layer-shell HUD to the primary display (`DP-1` / width >= 2560). Keep the secondary 60Hz display clean of layer-shell WebKit overhead.
- In `~/.config/hypr/looknfeel.lua`, always configure:
  ```lua
  hl.config({
    misc = {
      render_unfocused_fps = 60, -- Prevent dropping unfocused monitors to 15 FPS
    },
    cursor = {
      min_refresh_rate = 60,     -- Prevent cursor from throttling to 24 Hz
      no_hardware_cursors = true, -- Prevent NVIDIA hardware cursor plane judder
    },
  })
  ```

---

## 8. Strict Wake-Word-Only Chime Audio Discipline

### Symptom:
The system chimes repeatedly for every ambient room noise, keyboard stroke, chair shift, desk tap, or whenever TTS finishes talking, annoying the user.

### Root Cause:
`alfred_daemon.py` had audio cues attached to generic audio pipeline lifecycle events:
- Line 693 & 815: `self.play_chime(high=False)` was executed whenever a recording frame window completed, regardless of whether a wake word or even speech was present.
- Line 311: `self.play_chime(high=True)` was fired when TTS finished talking.
- Line 760: `self.play_chime(high=True)` was fired on microphone source detection.

### Strict Rule:
- The audible wake chime (`play_chime(high=True)`) must **ONLY** sound when the wake word regex (`WAKE_WORD_REGEX.search()`) matches a verified wake phrase ("Hey Computer", "Computer", "Hey Puter", "Puter") or when the user explicitly clicks the "🎙️ TALK" button.
- **NEVER** play audio chimes on VAD recording completion, ambient silence transitions, TTS completion, or audio routing changes.

---

## 9. Native 'Puter Companion App Launcher Integration

### Symptom:
Launching the "'Puter // Batcomputer" app from the application menu opens Zen Browser tabs/windows instead of the native companion app.

### Root Cause:
`launch-alfred.sh` had a fallback launching `zen-browser` when the background daemon was already active, rather than communicating with the active daemon to present the native PyQt6 `CompanionWidget`. Furthermore, `CompanionWidget` used `Qt.WindowType.Tool`, which Hyprland/Wayland failed to map as an `xdg_toplevel` floating client.

### Strict Rule:
- **NEVER** launch external web browsers (`zen-browser`, `chrome`, etc.) from [`launch-alfred.sh`](file:///home/jmrd/.gemini/antigravity/scratch/alfred-batcomputer/launch-alfred.sh).
- `CompanionWidget` must use `Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint` so Hyprland maps it to the matching window rule in `~/.config/hypr/hyprland.lua`:
  ```lua
  o.window({ title = "^Puter // BCv1.2 Companion$" }, { float = true, pin = true, size = { 420, 265 } })
  ```
- Interactive launches of [`launch-alfred.sh`](file:///home/jmrd/.gemini/antigravity/scratch/alfred-batcomputer/launch-alfred.sh) while the daemon is running must POST to `http://127.0.0.1:7778/local/companion` (`{"action": "show"}`) to smoothly raise the native companion widget.

