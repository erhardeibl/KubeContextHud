import subprocess
import tkinter as tk
import signal
import sys
import json
import os

# Default config values
default_config = {
    "font_family": "Arial",
    "font_size": 14,
    "text_color": "white",
    "background_color": "#2C3E50",
    "padding": 10,
    "context_label_text": "k8s ctx: ",
    "update_interval_ms": 500,
    "transparency": 0.8,
    "window_position": {
        "x": 0,
        "y": 0
    }
}

# Load config from config.json or create it with default values
config_file_path = 'config.json'
if not os.path.exists(config_file_path):
    with open(config_file_path, 'w') as config_file:
        json.dump(default_config, config_file, indent=4)

with open(config_file_path, 'r') as config_file:
    config = json.load(config_file)

font_family = config['font_family']
font_size = config['font_size']
text_color = config['text_color']
background_color = config['background_color']
padding = config['padding']
context_label_text = config['context_label_text']
update_interval_ms = config['update_interval_ms']
transparency = config['transparency']
window_position = config['window_position']

def get_current_context():
    result = subprocess.run(['kubectl', 'config', 'current-context'], stdout=subprocess.PIPE)
    return result.stdout.decode('utf-8').strip()

def update_context_label():
    current_context = get_current_context()
    context_label.config(text=f"{context_label_text}{current_context}")
    root.after(update_interval_ms, update_context_label)

def start_move(event):
    root.x = event.x
    root.y = event.y

def stop_move(event):
    root.x = None
    root.y = None
    save_window_position()

def on_motion(event):
    deltax = event.x - root.x
    deltay = event.y - root.y
    x = root.winfo_x() + deltax
    y = root.winfo_y() + deltay
    root.geometry(f"+{x}+{y}")

def save_window_position():
    config['window_position'] = {"x": root.winfo_x(), "y": root.winfo_y()}
    with open(config_file_path, 'w') as config_file:
        json.dump(config, config_file, indent=4)

def signal_handler(sig, frame):
    print("Exiting...")
    root.quit()
    sys.exit(0)

root = tk.Tk()
root.attributes("-topmost", True)  # Keep the window on top
root.overrideredirect(True)  # Hide the title bar
root.attributes("-alpha", transparency)  # Set window transparency

root.geometry(f"+{window_position['x']}+{window_position['y']}")

canvas = tk.Canvas(root, bg=background_color, highlightthickness=0)
canvas.pack(fill=tk.BOTH, expand=True)

context_label = tk.Label(canvas, text="", font=(font_family, font_size), fg=text_color, bg=background_color)
context_label.pack(padx=padding, pady=padding)

update_context_label()

root.bind("<ButtonPress-1>", start_move)
root.bind("<ButtonRelease-1>", stop_move)
root.bind("<B1-Motion>", on_motion)

signal.signal(signal.SIGINT, signal_handler)

root.mainloop()
