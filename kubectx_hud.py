"""Draggable, always-on-top HUD that shows the active Kubernetes context."""

from __future__ import annotations

import copy
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from typing import NamedTuple

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
ENCODING = "utf-8"

KUBECTL_TIMEOUT_S = 10.0
DRAIN_INTERVAL_MS = 100
REVEAL_TIMEOUT_MS = 3000
MIN_POLL_INTERVAL_MS = 50
EXIT_BUTTON_SIZE = 10
WINDOWS_CREATE_NO_WINDOW = 0x08000000

ERROR_KUBECTL_MISSING = "kubectl_missing_message"
ERROR_KUBECTL_TIMEOUT = "kubectl_timeout_message"
ERROR_KUBECTL_FAILED = "kubectl_error_message"

LEGACY_CONFIG_MARKER = "font_family"

DEFAULT_CONFIG = {
    "text_formatting": {
        "font_family": "Consolas",
        "font_size": 13,
        "padding": 10,
    },
    "language": "en",
    "languages": {
        "en": {
            "no_context_message": "No context",
            "kubectl_missing_message": "kubectl not found",
            "kubectl_timeout_message": "kubectl timed out",
            "kubectl_error_message": "Context unavailable",
            "pin_label": "Pin",
            "unpin_label": "Unpin",
            "exit_label": "Exit",
            "set_language_label": "Language",
            "set_color_scheme_label": "Color Scheme",
            "keep_on_top_label": "Keep on Top",
            "settings_label": "Settings",
            "contexts_label": "Contexts",
        },
        "de": {
            "no_context_message": "Kein Kontext",
            "kubectl_missing_message": "kubectl nicht gefunden",
            "kubectl_timeout_message": "kubectl antwortet nicht",
            "kubectl_error_message": "Kontext nicht verfügbar",
            "pin_label": "Anheften",
            "unpin_label": "Lösen",
            "exit_label": "Beenden",
            "set_language_label": "Sprache",
            "set_color_scheme_label": "Farbschema",
            "keep_on_top_label": "Immer im Vordergrund",
            "settings_label": "Einstellungen",
            "contexts_label": "Kontexte",
        },
    },
    "color_scheme": "light",
    "colors": {
        "dark": {
            "text_color": "white",
            "background_color": "#2C3E50",
            "exit_button_normal": "#8B0000",
            "exit_button_hover": "#FF6347",
        },
        "light": {
            "text_color": "black",
            "background_color": "#ECF0F1",
            "exit_button_normal": "#FF6347",
            "exit_button_hover": "#FF4500",
        },
        "solarized_dark": {
            "text_color": "#839496",
            "background_color": "#002B36",
            "exit_button_normal": "#DC322F",
            "exit_button_hover": "#CB4B16",
        },
        "solarized_light": {
            "text_color": "#657B83",
            "background_color": "#FDF6E3",
            "exit_button_normal": "#DC322F",
            "exit_button_hover": "#CB4B16",
        },
    },
    "behavior": {
        "update_interval_ms": 500,
        "window_transparency": 0.8,
        "hover_transparency": 1.0,
        "window_position": {"x": 0, "y": 0},
        "pinned": False,
        "keep_on_top": True,
    },
}


def deep_merge(base, override):
    """Recursively overlay override onto base without mutating either."""
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def diff_from_defaults(data, defaults):
    """Reduce data to only what differs from defaults, so config.json stays an override file."""
    result = {}
    for key, value in data.items():
        base = defaults.get(key) if isinstance(defaults, dict) else None
        if isinstance(value, dict) and isinstance(base, dict):
            nested = diff_from_defaults(value, base)
            if nested:
                result[key] = nested
        elif not isinstance(defaults, dict) or key not in defaults or value != base:
            result[key] = value
    return result


class Config:
    """User overrides layered on top of DEFAULT_CONFIG."""

    def __init__(self, path, data):
        self.path = path
        self.data = data
        self.needs_rewrite = False

    @classmethod
    def load(cls, path):
        data = copy.deepcopy(DEFAULT_CONFIG)
        stored = cls._read(path)
        legacy = LEGACY_CONFIG_MARKER in stored
        if legacy:
            stored = {}  # pre-nested schema; its keys would corrupt the merged tree
        if stored:
            data = deep_merge(data, stored)
        config = cls(path, data)
        config.needs_rewrite = legacy or stored != diff_from_defaults(data, DEFAULT_CONFIG)
        return config

    @staticmethod
    def _read(path):
        try:
            content = path.read_text(encoding=ENCODING)
        except FileNotFoundError:
            return {}
        except OSError as error:
            print(f"Cannot read {path}: {error}", file=sys.stderr)
            return {}
        try:
            user = json.loads(content)
        except json.JSONDecodeError as error:
            print(f"Ignoring malformed {path}: {error}", file=sys.stderr)
            return {}
        return user if isinstance(user, dict) else {}

    def save(self):
        payload = diff_from_defaults(self.data, DEFAULT_CONFIG)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(
                json.dumps(payload, indent=4, ensure_ascii=False),
                encoding=ENCODING,
            )
            os.replace(temporary, self.path)
            self.needs_rewrite = False
        except OSError as error:
            print(f"Cannot save {self.path}: {error}", file=sys.stderr)
            temporary.unlink(missing_ok=True)


class ContextSnapshot(NamedTuple):
    current: str | None
    contexts: tuple[str, ...]
    error: str | None


EMPTY_SNAPSHOT = ContextSnapshot(None, (), None)


def _subprocess_kwargs():
    if os.name == "nt":
        return {"creationflags": WINDOWS_CREATE_NO_WINDOW}
    return {}


def run_kubectl(arguments):
    """Return (stdout, error_key); error_key is None on success."""
    try:
        result = subprocess.run(
            ["kubectl", *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=KUBECTL_TIMEOUT_S,
            check=False,
            **_subprocess_kwargs(),
        )
    except FileNotFoundError:
        return "", ERROR_KUBECTL_MISSING
    except subprocess.TimeoutExpired:
        return "", ERROR_KUBECTL_TIMEOUT
    except OSError as error:
        print(f"kubectl failed: {error}", file=sys.stderr)
        return "", ERROR_KUBECTL_FAILED

    if result.returncode != 0:
        message = result.stderr.decode(ENCODING, errors="replace").strip()
        print(f"kubectl exited with {result.returncode}: {message}", file=sys.stderr)
        return "", ERROR_KUBECTL_FAILED
    return result.stdout.decode(ENCODING, errors="replace"), None


def read_contexts():
    """Read the current context and the full context list in one kubectl call."""
    output, error = run_kubectl(["config", "view", "-o", "json"])
    if error:
        return ContextSnapshot(None, (), error)
    try:
        document = json.loads(output)
    except json.JSONDecodeError:
        return ContextSnapshot(None, (), ERROR_KUBECTL_FAILED)

    entries = document.get("contexts") or []
    names = tuple(
        str(entry["name"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("name")
    )
    return ContextSnapshot(document.get("current-context") or None, names, None)


def kubeconfig_paths():
    configured = os.environ.get("KUBECONFIG")
    if configured:
        return [Path(part) for part in configured.split(os.pathsep) if part]
    return [Path.home() / ".kube" / "config"]


def kubeconfig_fingerprint():
    """Cheap stat-based signature used to skip kubectl calls when nothing changed."""
    fingerprint = []
    for path in kubeconfig_paths():
        try:
            status = path.stat()
        except OSError:
            fingerprint.append((str(path), None, None))
        else:
            fingerprint.append((str(path), status.st_mtime_ns, status.st_size))
    return tuple(fingerprint)


class KubectlWorker(threading.Thread):
    """Runs kubectl off the UI thread and publishes snapshots to a queue."""

    def __init__(self, results, interval_ms):
        super().__init__(name="kubectl-worker", daemon=True)
        self._results = results
        self._interval_s = max(int(interval_ms), MIN_POLL_INTERVAL_MS) / 1000.0
        self._commands = queue.Queue()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._fingerprint = None
        self._snapshot = None

    def stop(self):
        self._stopping.set()
        self._wake.set()

    def use_context(self, name):
        self._commands.put(name)
        self._wake.set()

    def run(self):
        while not self._stopping.is_set():
            self._run_commands()
            if self._stopping.is_set():
                break
            self._poll()
            self._wake.wait(self._interval_s)
            self._wake.clear()

    def _run_commands(self):
        while True:
            try:
                name = self._commands.get_nowait()
            except queue.Empty:
                return
            _, error = run_kubectl(["config", "use-context", name])
            self._fingerprint = None
            if error:
                self._publish((self._snapshot or EMPTY_SNAPSHOT)._replace(error=error))

    def _poll(self):
        fingerprint = kubeconfig_fingerprint()
        if self._snapshot is not None and fingerprint == self._fingerprint:
            return
        snapshot = read_contexts()
        self._fingerprint = fingerprint if snapshot.error is None else None
        self._publish(snapshot)

    def _publish(self, snapshot):
        if snapshot != self._snapshot:
            self._snapshot = snapshot
            self._results.put(snapshot)


def enable_dpi_awareness():
    """Render at the monitor's real resolution instead of being bitmap-stretched by Windows."""
    if os.name != "nt":
        return
    import ctypes

    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (OSError, AttributeError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except (OSError, AttributeError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (OSError, AttributeError):
        pass


def monitor_dpi(root):
    """DPI of the monitor currently showing the window."""
    if os.name == "nt":
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
            monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
            x, y = ctypes.c_uint(), ctypes.c_uint()
            if (
                ctypes.windll.shcore.GetDpiForMonitor(
                    monitor, 0, ctypes.byref(x), ctypes.byref(y)
                )
                == 0
            ):
                return float(x.value)
        except (OSError, AttributeError):
            pass
    return root.winfo_fpixels("1i")


def screen_bounds(root):
    """Bounds of the whole virtual desktop so multi-monitor positions survive."""
    if os.name == "nt":
        try:
            import ctypes

            metric = ctypes.windll.user32.GetSystemMetrics
            left, top, width, height = metric(76), metric(77), metric(78), metric(79)
            if width > 0 and height > 0:
                return left, top, width, height
        except (OSError, AttributeError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def clamp_position(x, y, width, height, bounds):
    left, top, span_x, span_y = bounds
    x = left if width >= span_x else min(max(x, left), left + span_x - width)
    y = top if height >= span_y else min(max(y, top), top + span_y - height)
    return x, y


def scheme_colors(config):
    schemes = config["colors"]
    return schemes.get(config["color_scheme"]) or next(iter(schemes.values()))


class Hud:
    """The HUD window. Every Tk call happens on the thread that owns this object."""

    def __init__(self, config):
        self.config = config
        self.snapshot = EMPTY_SNAPSHOT
        self.results = queue.Queue()

        self._drag_offset = None
        self._drag_origin = None
        self._pressed = False
        self._menu_contexts = None
        self._menu_posted = False
        self._hovering = False
        self._visible = False
        self._drain_job = None
        self._closing = False
        self._geometry = None
        self.menu = None
        self.contexts_menu = None
        self.settings_menu = None

        behavior = config.data["behavior"]
        self.pinned = bool(behavior["pinned"])
        self.idle_alpha = float(behavior["window_transparency"])
        self.hover_alpha = float(behavior["hover_transparency"])

        self.root = tk.Tk()
        self.root.title("Kubernetes Context HUD")
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", self.idle_alpha)

        self.keep_on_top = tk.BooleanVar(value=bool(behavior["keep_on_top"]))
        self.root.attributes("-topmost", self.keep_on_top.get())

        self.language_var = tk.StringVar(value=config.data["language"])
        self.color_scheme_var = tk.StringVar(value=config.data["color_scheme"])
        self.context_var = tk.StringVar(value="")

        formatting = config.data["text_formatting"]
        self._base_font_size = int(formatting["font_size"])
        self._base_padding = int(formatting["padding"])
        self._dpi = monitor_dpi(self.root)
        self.scale = self._dpi / 96.0
        self.padding = round(self._base_padding * self.scale)
        self.font = tkfont.Font(
            family=formatting["font_family"],
            size=-round(self._base_font_size * self._dpi / 72.0),
        )
        self.label = tk.Label(self.root, text="", font=self.font, anchor=tk.CENTER)
        self.label.pack(fill=tk.BOTH, expand=True, padx=self.padding, pady=self.padding)

        button_size = round(EXIT_BUTTON_SIZE * self.scale)
        self.exit_button = tk.Frame(self.root, width=button_size, height=button_size)
        self.exit_button.place_forget()

        self.apply_style()
        self.rebuild_menu()
        self.update_label()
        self.bind_events()
        self.apply_geometry()
        self.root.withdraw()

        self.worker = KubectlWorker(self.results, behavior["update_interval_ms"])

    def text(self, key):
        languages = self.config.data["languages"]
        selected = languages.get(self.config.data["language"])
        if selected is not None and key in selected:
            return selected[key]
        return DEFAULT_CONFIG["languages"]["en"].get(key, key)

    def style(self):
        return scheme_colors(self.config.data)

    def bind_events(self):
        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<ButtonRelease-1>", self.stop_move)
        self.root.bind("<B1-Motion>", self.on_motion)
        self.root.bind("<Button-3>", self.show_menu)
        self.root.bind("<Enter>", self.on_enter)
        self.root.bind("<Leave>", self.on_leave)
        self.exit_button.bind("<Button-1>", self.on_exit_click)
        self.exit_button.bind("<Enter>", self.on_exit_enter)
        self.exit_button.bind("<Leave>", self.on_exit_leave)

    def apply_style(self):
        style = self.style()
        self.root.configure(bg=style["background_color"])
        self.label.configure(
            fg=style["text_color"], bg=style["background_color"]
        )
        self.exit_button.configure(bg=style["exit_button_normal"])

    def label_text(self):
        if self.snapshot.error:
            return self.text(self.snapshot.error)
        return self.snapshot.current or self.text("no_context_message")

    def measure(self):
        """Size to exactly the current context, shown in full."""
        width = self.font.measure(self.label_text())
        height = self.font.metrics("linespace")
        return width + 2 * self.padding + 2, height + 2 * self.padding + 2

    def apply_geometry(self):
        width, height = self.measure()
        position = self.config.data["behavior"]["window_position"]
        x, y = clamp_position(
            int(position.get("x", 0)),
            int(position.get("y", 0)),
            width,
            height,
            screen_bounds(self.root),
        )
        geometry = f"{width}x{height}+{x}+{y}"
        if geometry != self._geometry:
            self.root.geometry(geometry)
            self._geometry = geometry

    def rebuild_menu(self):
        if self.menu is not None:
            self.menu.destroy()

        menu = tk.Menu(self.root, tearoff=0)
        self.contexts_menu = tk.Menu(menu, tearoff=0)
        menu.add_cascade(label=self.text("contexts_label"), menu=self.contexts_menu)
        menu.add_separator()
        menu.add_command(
            label=self.text("unpin_label") if self.pinned else self.text("pin_label"),
            command=self.toggle_pin,
        )
        self.settings_menu = self.build_settings_menu(menu)
        menu.add_cascade(label=self.text("settings_label"), menu=self.settings_menu)
        menu.add_separator()
        menu.add_command(label=self.text("exit_label"), command=self.shutdown)

        self.menu = menu
        self._menu_contexts = None
        self.refresh_contexts_menu()

    def build_settings_menu(self, parent):
        settings = tk.Menu(parent, tearoff=0)
        settings.add_checkbutton(
            label=self.text("keep_on_top_label"),
            variable=self.keep_on_top,
            command=self.toggle_keep_on_top,
        )

        language_menu = tk.Menu(settings, tearoff=0)
        for name in sorted(self.config.data["languages"]):
            language_menu.add_radiobutton(
                label=name.upper(),
                variable=self.language_var,
                value=name,
                command=lambda selected=name: self.set_language(selected),
            )
        settings.add_cascade(label=self.text("set_language_label"), menu=language_menu)

        scheme_menu = tk.Menu(settings, tearoff=0)
        for name in sorted(self.config.data["colors"]):
            scheme_menu.add_radiobutton(
                label=name.replace("_", " ").title(),
                variable=self.color_scheme_var,
                value=name,
                command=lambda selected=name: self.set_color_scheme(selected),
            )
        settings.add_cascade(
            label=self.text("set_color_scheme_label"), menu=scheme_menu
        )
        return settings

    def refresh_contexts_menu(self):
        if self._menu_posted or self._menu_contexts == self.snapshot.contexts:
            return
        self.contexts_menu.delete(0, tk.END)
        for name in self.snapshot.contexts:
            self.contexts_menu.add_radiobutton(
                label=name,
                variable=self.context_var,
                value=name,
                command=lambda selected=name: self.request_context(selected),
            )
        self._menu_contexts = self.snapshot.contexts

    def request_context(self, name):
        self.worker.use_context(name)

    def update_label(self):
        self.label.configure(text=self.label_text())

    def refresh_scaling(self):
        """Follow Windows scaling changes and moves between monitors of differing DPI."""
        dpi = monitor_dpi(self.root)
        if abs(dpi - self._dpi) < 0.5:
            return
        self._dpi = dpi
        self.scale = dpi / 96.0
        # Menus use Tk's point-based system fonts, which track the scaling factor.
        self.root.tk.call("tk", "scaling", dpi / 72.0)
        self.padding = round(self._base_padding * self.scale)
        self.font.configure(size=-round(self._base_font_size * dpi / 72.0))
        self.label.pack_configure(padx=self.padding, pady=self.padding)
        button_size = round(EXIT_BUTTON_SIZE * self.scale)
        self.exit_button.configure(width=button_size, height=button_size)
        self._geometry = None
        self.apply_geometry()

    def drain_results(self):
        updated = False
        while True:
            try:
                self.snapshot = self.results.get_nowait()
            except queue.Empty:
                break
            updated = True
        self.refresh_scaling()
        if updated:
            self.context_var.set(self.snapshot.current or "")
            self.update_label()
            self.apply_style()
            self.apply_geometry()
            self.refresh_contexts_menu()
            self.reveal()
        self._drain_job = self.root.after(DRAIN_INTERVAL_MS, self.drain_results)

    def reveal(self):
        """Stay hidden until sized from real data, so the window never resizes on screen."""
        if self._visible or self._closing:
            return
        self._visible = True
        self.root.deiconify()
        self.root.attributes("-topmost", self.keep_on_top.get())

    def set_language(self, name):
        self.config.data["language"] = name
        self.config.save()
        self.update_label()
        self.apply_geometry()
        self.root.after_idle(self.rebuild_menu)

    def set_color_scheme(self, name):
        self.config.data["color_scheme"] = name
        self.config.save()
        self.apply_style()

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.config.data["behavior"]["pinned"] = self.pinned
        self.config.save()
        self.root.after_idle(self.rebuild_menu)

    def toggle_keep_on_top(self):
        value = self.keep_on_top.get()
        self.root.attributes("-topmost", value)
        self.config.data["behavior"]["keep_on_top"] = value
        self.config.save()

    def post_menu(self, menu, event):
        self._menu_posted = True
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            self._menu_posted = False

    def show_menu(self, event):
        self.post_menu(self.menu, event)

    def start_move(self, event):
        self._pressed = True
        self._drag_origin = (self.root.winfo_x(), self.root.winfo_y())
        if self.pinned:
            self._drag_offset = None
            return
        self._drag_offset = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )

    def on_motion(self, event):
        if self.pinned or self._drag_offset is None:
            return
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        self.root.geometry(f"+{x}+{y}")

    def stop_move(self, _event):
        if not self._pressed:
            return
        self._pressed = False
        self._drag_offset = None
        if (self.root.winfo_x(), self.root.winfo_y()) != self._drag_origin:
            self.save_position()

    def save_position(self):
        self.config.data["behavior"]["window_position"] = {
            "x": self.root.winfo_x(),
            "y": self.root.winfo_y(),
        }
        self._geometry = None
        self.config.save()

    def on_enter(self, _event):
        self.set_hover(True)

    def on_leave(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None or widget.winfo_toplevel() is not self.root:
            self.set_hover(False)

    def set_hover(self, hovering):
        """Enter and Leave fire on every child crossing, so only act on real transitions."""
        if hovering == self._hovering:
            return
        self._hovering = hovering
        self.root.attributes("-alpha", self.hover_alpha if hovering else self.idle_alpha)
        if hovering:
            self.exit_button.place(x=0, y=0)
        else:
            self.exit_button.place_forget()

    def on_exit_enter(self, _event):
        self.exit_button.configure(bg=self.style()["exit_button_hover"])

    def on_exit_leave(self, _event):
        self.exit_button.configure(bg=self.style()["exit_button_normal"])

    def on_exit_click(self, _event):
        self.shutdown()

    def shutdown(self, *_args):
        if self._closing:
            return
        self._closing = True
        self.worker.stop()
        if self._drain_job is not None:
            self.root.after_cancel(self._drain_job)
            self._drain_job = None
        self.root.destroy()

    def run(self):
        signal.signal(signal.SIGINT, lambda *_: self.shutdown())
        self.worker.start()
        self.drain_results()
        self.root.after(REVEAL_TIMEOUT_MS, self.reveal)
        self.root.mainloop()


def main():
    enable_dpi_awareness()
    config = Config.load(CONFIG_PATH)
    if config.needs_rewrite:
        config.save()
    Hud(config).run()


if __name__ == "__main__":
    main()
