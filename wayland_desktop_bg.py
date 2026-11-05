#!/usr/bin/env python3
"""
Wayland Layer-Shell Desktop Background for Batcomputer BCv1.2
Renders the interactive Batcomputer HUD as a native Wayland Layer Surface on Layer.BOTTOM
across all connected monitors. Sits strictly beneath all application windows (Antigravity,
terminals, browsers, etc.) without tiling, without stealing window space, and without
obscuring any open apps.
"""

import os
import sys
import json
import fcntl
import tempfile
from pathlib import Path

# Allow WebKitGTK to utilize native GPU hardware composition with NVIDIA 580 driver
# If DMA-BUF is disabled, WebKit forces software SHM blitting of 2560x1440 @ 144Hz, consuming 100% CPU.
# os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"] = "1"
# os.environ["WEBKIT_DMABUF_RENDERER_FORCE_SHM"] = "1"

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, Gdk, GtkLayerShell, WebKit2, GLib

BASE_DIR = Path(__file__).resolve().parent

def get_auth_token() -> str:
    token = os.environ.get("BATCOMPUTER_AUTH_TOKEN", "")
    if token:
        return token
    auth_file = BASE_DIR / "auth_config.json"
    if auth_file.exists():
        try:
            cfg = json.loads(auth_file.read_text(encoding="utf-8"))
            return cfg.get("daemon_token", "")
        except Exception:
            pass
    return ""

class WaylandDesktopShellManager:
    def __init__(self, bridge_port: int = 7778):
        self.bridge_port = bridge_port
        self.token = get_auth_token()
        self.display = Gdk.Display.get_default()
        self.windows = []
        self._monitor_timer_id = None

        if not self.display:
            print("❌ [Wayland Layer Shell]: No default GDK display found.")
            sys.exit(1)

        self.setup_screens()
        self.display.connect("monitor-added", lambda d, m: self.on_monitor_changed())
        self.display.connect("monitor-removed", lambda d, m: self.on_monitor_changed())

    def on_monitor_changed(self):
        if self._monitor_timer_id is not None:
            GLib.source_remove(self._monitor_timer_id)
        self._monitor_timer_id = GLib.timeout_add(800, self._debounced_setup_screens)

    def _debounced_setup_screens(self):
        self._monitor_timer_id = None
        print("🖥️ [Wayland Layer Shell]: Monitor configuration settled. Re-initializing surfaces...")
        self.setup_screens()
        return False

    def cleanup_windows(self):
        for win in self.windows:
            try:
                win.destroy()
            except Exception:
                pass
        self.windows.clear()

    def setup_screens(self):
        self.cleanup_windows()
        n_mon = self.display.get_n_monitors()
        print(f"🦇 [Wayland Layer Shell]: Initializing interactive desktop background on {n_mon} monitors...")

        for i in range(n_mon):
            mon = self.display.get_monitor(i)
            m_model = mon.get_model() or f"DP-{i+1}"
            geom = mon.get_geometry()
            
            # On multi-monitor setups, run the heavy layer-shell HUD exclusively on the primary monitor
            # (DP-1 / 1440p) unless explicitly forced. Running WebKit software SHM layers across
            # secondary 60Hz screens causes severe compositing stutter and 100% CPU lockups.
            target_only_primary = os.environ.get("BATCOMPUTER_ALL_MONITORS", "0") != "1"
            if target_only_primary and geom.width < 2560 and n_mon > 1:
                print(f"   ⏩ Skipping secondary monitor {i}: {m_model} ({geom.width}x{geom.height}) to ensure 60Hz screen remains silky smooth.")
                continue

            print(f"   ▶ Monitor {i}: {m_model} ({geom.width}x{geom.height} at {geom.x},{geom.y})")

            win = Gtk.Window()
            GtkLayerShell.init_for_window(win)
            GtkLayerShell.set_layer(win, GtkLayerShell.Layer.BOTTOM)
            GtkLayerShell.set_namespace(win, f"batcomputer-desktop-{i}")
            GtkLayerShell.set_monitor(win, mon)

            # Anchor to all 4 edges so it covers the monitor 100%
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(win, edge, True)

            # -1 exclusive zone means compositor does not reserve panels / it acts as desktop background
            GtkLayerShell.set_exclusive_zone(win, -1)
            # KeyboardMode.ON_DEMAND allows clicks and interactive text/button input on the desktop HUD
            GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.ON_DEMAND)

            # WebKit Web View
            web = WebKit2.WebView()
            settings = web.get_settings()
            settings.set_enable_webgl(False)
            settings.set_enable_2d_canvas_acceleration(True)
            settings.set_enable_media_stream(True)
            settings.set_enable_webrtc(True)
            settings.set_enable_smooth_scrolling(False)
            settings.set_allow_file_access_from_file_urls(True)
            settings.set_allow_universal_access_from_file_urls(True)

            # Prevent viewport scrolling and disable heavy CPU backdrop blur filters on software layer-shell HUD
            ucm = web.get_user_content_manager()
            user_style = WebKit2.UserStyleSheet(
                "html, body { overflow: hidden !important; height: 100vh !important; max-height: 100vh !important; position: fixed !important; inset: 0 !important; overscroll-behavior: none !important; } * { backdrop-filter: none !important; }",
                WebKit2.UserContentInjectedFrames.ALL_FRAMES,
                WebKit2.UserStyleLevel.USER,
                None,
                None
            )
            ucm.add_style_sheet(user_style)

            def on_web_process_terminated(w, reason):
                print(f"⚠️ [Desktop Background]: Web process terminated ({reason}). Auto-reloading webview in 1s...")
                GLib.timeout_add(1000, lambda: (w.reload(), False)[1])

            def on_permission_request(w, request):
                # Auto-grant mic / media / capture requests
                request.allow()
                return True

            def on_script_dialog(w, dialog):
                # Suppress JavaScript alert/confirm/prompt dialogs from blocking layer-shell HUD
                dialog.confirm()
                return True

            web.connect("web-process-terminated", on_web_process_terminated)
            web.connect("permission-request", on_permission_request)
            web.connect("script-dialog", on_script_dialog)

            token_arg = f"&token={self.token}" if self.token else ""
            target_url = f"http://127.0.0.1:{self.bridge_port}/?desktop=1&screen={m_model}{token_arg}"
            web.load_uri(target_url)

            win.add(web)
            win.show_all()
            self.windows.append(win)

        print("✅ [Wayland Layer Shell]: Desktop background active beneath all application windows.")

_BG_LOCK_FD = None

def acquire_single_instance_lock() -> bool:
    global _BG_LOCK_FD
    lock_file = Path(tempfile.gettempdir()) / "wayland_desktop_bg.lock"
    try:
        _BG_LOCK_FD = open(lock_file, "w")
        fcntl.flock(_BG_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _BG_LOCK_FD.write(f"{os.getpid()}\n")
        _BG_LOCK_FD.flush()
        return True
    except (BlockingIOError, OSError):
        return False

def main():
    if not acquire_single_instance_lock():
        print("🦇 [Desktop Background]: Another Wayland Layer Shell instance is already running. Exiting duplicate.")
        sys.exit(0)
    port = int(os.environ.get("ALFRED_LOCAL_BRIDGE_PORT", "7778"))
    manager = WaylandDesktopShellManager(bridge_port=port)
    try:
        Gtk.main()
    except KeyboardInterrupt:
        print("\n[Desktop Background Exiting]")

if __name__ == "__main__":
    main()
