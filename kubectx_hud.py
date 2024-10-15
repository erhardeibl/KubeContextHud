import subprocess
import tkinter as tk
import signal
import sys

# Config
font_size = 14
text_color = "white"
font_family = "Arial"
background_color = "#2C3E50"
padding = 10
update_interval_ms = 500

def get_current_context():
    result = subprocess.run(['kubectl', 'config', 'current-context'], stdout=subprocess.PIPE)
    return result.stdout.decode('utf-8').strip()

def update_context_label():
    current_context = get_current_context()
    context_label.config(text=f"Current Context: {current_context}")
    root.after(update_interval_ms, update_context_label)

def start_move(event):
    root.x = event.x
    root.y = event.y

def stop_move(event):
    root.x = None
    root.y = None

def on_motion(event):
    deltax = event.x - root.x
    deltay = event.y - root.y
    x = root.winfo_x() + deltax
    y = root.winfo_y() + deltay
    root.geometry(f"+{x}+{y}")

def signal_handler(sig, frame):
    print("Exiting...")
    root.quit()
    sys.exit(0)

root = tk.Tk()
root.attributes("-topmost", True)  # Keep the window on top
root.overrideredirect(True)  # Hide the title bar

canvas = tk.Canvas(root, bg=background_color, highlightthickness=0)
canvas.pack(fill=tk.BOTH, expand=True)

context_label = tk.Label(canvas, text="", font=(font_family, font_size), fg=text_color, bg=background_color)
context_label.pack(padx=padding, pady=padding)

update_context_label()

root.bind("<Button-1>", start_move)
root.bind("<ButtonRelease-1>", stop_move)
root.bind("<B1-Motion>", on_motion)

signal.signal(signal.SIGINT, signal_handler)

root.mainloop()