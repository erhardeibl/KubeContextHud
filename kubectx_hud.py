import subprocess
import tkinter as tk
import signal
import sys
import json
import os

# Constants for event bindings
EVENT_START_MOVE = "<ButtonPress-1>"
EVENT_STOP_MOVE = "<ButtonRelease-1>"
EVENT_ON_MOTION = "<B1-Motion>"
EVENT_SHOW_CONTEXT_MENU = "<Button-3>"
EVENT_ON_ENTER = "<Enter>"
EVENT_ON_LEAVE = "<Leave>"
EVENT_EXIT_BUTTON_CLICK = "<Button-1>"
EVENT_EXIT_BUTTON_ENTER = "<Enter>"
EVENT_EXIT_BUTTON_LEAVE = "<Leave>"

# File paths
CONFIG_FILE_PATH = 'config.json'
DEFAULT_CONFIG_FILE_PATH = 'default_config.json'
ENCODING = 'utf-8'

def load_config():
    """Load default configuration and update with values from config.json."""
    try:
        with open(DEFAULT_CONFIG_FILE_PATH, 'r', encoding=ENCODING) as default_config_file:
            config = json.load(default_config_file)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error loading {DEFAULT_CONFIG_FILE_PATH}: {e}")
        sys.exit(1)  # Exit if default configuration fails

    try:
        with open(CONFIG_FILE_PATH, 'r', encoding=ENCODING) as config_file:
            user_config = json.load(config_file)
            config = update_config(config, user_config)  # Update default config with user config
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error loading {CONFIG_FILE_PATH}: {e}")

    return config

def update_config(default_config, user_config):
    """Update default config with user config values."""
    for key, value in user_config.items():
        if isinstance(value, dict) and key in default_config:
            default_config[key] = update_config(default_config[key], value)
        elif key in default_config:
            default_config[key] = value
    return default_config

def get_current_context():
    """Get the current Kubernetes context."""
    try:
        result = subprocess.run(['kubectl', 'config', 'current-context'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        return result.stdout.decode('utf-8').strip()
    except subprocess.CalledProcessError:
        return None

def get_available_contexts():
    """Get the available Kubernetes contexts."""
    try:
        result = subprocess.run(['kubectl', 'config', 'get-contexts', '-o', 'name'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        return result.stdout.decode('utf-8').strip().split('\n')
    except subprocess.CalledProcessError:
        return []

def set_k8s_context(context):
    """Set the Kubernetes context."""
    try:
        result = subprocess.run(['kubectl', 'config', 'use-context', context], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        update_context_label()
    except subprocess.CalledProcessError as e:
        print(f"Error setting context: {e}")

def update_context_label():
    """Update the context label with the current Kubernetes context."""
    current_context = get_current_context()
    if current_context:
        context_label.config(text=f"{context_label_text}{context_label_delimiter}{current_context}")
        k8s_context_var.set(current_context)
    else:
        context_label.config(text=f"{context_label_text}{context_label_delimiter}{no_context_message}")
    root.after(update_interval_ms, update_context_label)

def start_move(event):
    """Start moving the window."""
    if not pinned:
        root.x = event.x
        root.y = event.y

def stop_move(event):
    """Stop moving the window."""
    if not pinned:
        root.x = None
        root.y = None
        save_window_position()

def on_motion(event):
    """Handle window motion."""
    if not pinned:
        deltax = event.x - root.x
        deltay = event.y - root.y
        x = root.winfo_x() + deltax
        y = root.winfo_y() + deltay
        root.geometry(f"+{x}+{y}")

def save_window_position():
    """Save the window position to the configuration."""
    config['behavior']['window_position'] = {"x": root.winfo_x(), "y": root.winfo_y()}
    with open(CONFIG_FILE_PATH, 'w', encoding=ENCODING) as config_file:
        json.dump(config, config_file, indent=4, ensure_ascii=False)

def signal_handler(sig, frame):
    """Handle the SIGINT signal."""
    print("Exiting...")
    root.quit()
    sys.exit(0)

def toggle_pin():
    """Toggle the pinned state of the window."""
    global pinned
    pinned = not pinned
    config['behavior']['pinned'] = pinned
    with open(CONFIG_FILE_PATH, 'w', encoding=ENCODING) as config_file:
        json.dump(config, config_file, indent=4, ensure_ascii=False)
    update_context_menu()

def toggle_keep_on_top():
    """Toggle the keep on top state of the window."""
    global keep_on_top
    keep_on_top = not keep_on_top
    config['behavior']['keep_on_top'] = keep_on_top
    root.attributes("-topmost", keep_on_top)
    with open(CONFIG_FILE_PATH, 'w', encoding=ENCODING) as config_file:
        json.dump(config, config_file, indent=4, ensure_ascii=False)
    update_context_menu()

def update_context_menu():
    """Update the context menu labels."""
    context_menu.entryconfig(0, label=exit_label)
    context_menu.entryconfig(1, label=unpin_label if pinned else pin_label)
    context_menu.entryconfig(2, label=keep_on_top_label)
    context_menu.entryconfig(3, label=set_language_label)
    context_menu.entryconfig(4, label=set_color_scheme_label)
    context_menu.entryconfig(5, label=context_label_text)

    # Update the k8s context menu
    k8s_context_menu.delete(0, tk.END)
    current_context = get_current_context()
    for context in get_available_contexts():
        k8s_context_menu.add_radiobutton(label=context, variable=k8s_context_var, value=context, command=lambda c=context: set_k8s_context(c))

def show_context_menu(event):
    """Show the context menu."""
    context_menu.post(event.x_root, event.y_root)

def on_exit_button_click(event):
    """Handle the exit button click."""
    root.quit()
    sys.exit(0)

def on_enter(event):
    """Handle mouse enter event."""
    exit_button.place(x=0, y=0)

def on_leave(event):
    """Handle mouse leave event."""
    if event.widget != exit_button:
        exit_button.place_forget()

def on_exit_button_enter(event):
    """Handle mouse enter event on the exit button."""
    exit_button.config(bg=exit_button_hover)
    exit_button.place(x=0, y=0)

def on_exit_button_leave(event):
    """Handle mouse leave event on the exit button."""
    exit_button.config(bg=exit_button_normal)
    exit_button.place_forget()

def set_language(lang):
    """Set the language for the UI."""
    global language, context_label_text, context_label_delimiter, no_context_message, pin_label, unpin_label, exit_label, set_language_label, set_color_scheme_label, keep_on_top_label
    language = config['language'][lang]
    context_label_text = language['context_label_text']
    context_label_delimiter = language['context_label_delimiter']
    no_context_message = language['no_context_message']
    pin_label = language['pin_label']
    unpin_label = language['unpin_label']
    exit_label = language['exit_label']
    set_language_label = language['set_language_label']
    set_color_scheme_label = language['set_color_scheme_label']
    keep_on_top_label = language['keep_on_top_label']
    update_context_menu()
    update_context_label()

def set_color_scheme(scheme):
    """Set the color scheme for the UI."""
    global colors, text_color, background_color, exit_button_normal, exit_button_hover
    config['color_scheme'] = scheme
    colors = config['colors'][scheme]
    text_color = colors['text_color']
    background_color = colors['background_color']
    exit_button_normal = colors['exit_button_normal']
    exit_button_hover = colors['exit_button_hover']
    canvas.config(bg=background_color)
    context_label.config(fg=text_color, bg=background_color)
    exit_button.config(bg=exit_button_normal)
    color_scheme_var.set(scheme)
    update_context_menu()

# Load configuration
config = load_config()

# Extract config values
text_formatting = config['text_formatting']
language = config['language']['en']  # Default to English
color_scheme = config['color_scheme']
colors = config['colors'][color_scheme]
behavior = config['behavior']

# Extract text formatting values
font_family = text_formatting['font_family']
font_size = text_formatting['font_size']
padding = text_formatting['padding']

# Extract language values
context_label_text = language['context_label_text']
context_label_delimiter = language['context_label_delimiter']
no_context_message = language['no_context_message']
pin_label = language['pin_label']
unpin_label = language['unpin_label']
exit_label = language['exit_label']
set_language_label = language['set_language_label']
set_color_scheme_label = language['set_color_scheme_label']
keep_on_top_label = language['keep_on_top_label']

# Extract color values
text_color = colors['text_color']
background_color = colors['background_color']
exit_button_normal = colors['exit_button_normal']
exit_button_hover = colors['exit_button_hover']

# Extract behavior values
update_interval_ms = behavior['update_interval_ms']
window_transparency = behavior['window_transparency']
window_position = behavior['window_position']
pinned = behavior['pinned']
keep_on_top = behavior['keep_on_top']

# Initialize the main window
root = tk.Tk()
keep_on_top = tk.BooleanVar(value=keep_on_top)  # Bind the variable to the checkbutton
root.attributes("-topmost", keep_on_top.get())  # Keep the window on top
root.overrideredirect(True)  # Hide the title bar
root.attributes("-alpha", window_transparency)  # Set window transparency
root.geometry(f"+{window_position['x']}+{window_position['y']}")

# Create the canvas and context label
canvas = tk.Canvas(root, bg=background_color, highlightthickness=0)
canvas.pack(fill=tk.BOTH, expand=True)
context_label = tk.Label(canvas, text="", font=(font_family, font_size), fg=text_color, bg=background_color)
context_label.pack(padx=padding, pady=padding)

# Create the exit button
exit_button = tk.Frame(root, bg=exit_button_normal, width=10, height=10)
exit_button.place_forget()
exit_button.bind(EVENT_EXIT_BUTTON_CLICK, on_exit_button_click)
exit_button.bind(EVENT_EXIT_BUTTON_ENTER, on_exit_button_enter)
exit_button.bind(EVENT_EXIT_BUTTON_LEAVE, on_exit_button_leave)

# Bind events
root.bind(EVENT_START_MOVE, start_move)
root.bind(EVENT_STOP_MOVE, stop_move)
root.bind(EVENT_ON_MOTION, on_motion)
root.bind(EVENT_SHOW_CONTEXT_MENU, show_context_menu)
canvas.bind(EVENT_ON_ENTER, on_enter)
canvas.bind(EVENT_ON_LEAVE, on_leave)

# Set up signal handler
signal.signal(signal.SIGINT, signal_handler)

# Create the context menu
context_menu = tk.Menu(root, tearoff=0)
context_menu.add_command(label=exit_label, command=root.quit)
context_menu.add_command(label=pin_label, command=toggle_pin)
context_menu.add_checkbutton(label=keep_on_top_label, variable=keep_on_top, command=toggle_keep_on_top)

# Add language options to the context menu
language_menu = tk.Menu(context_menu, tearoff=0)
language_var = tk.StringVar(value='en')
for lang in config['language'].keys():
    language_menu.add_radiobutton(label=lang.upper(), variable=language_var, value=lang, command=lambda l=lang: set_language(l))
context_menu.add_cascade(label=set_language_label, menu=language_menu)

# Add color scheme options to the context menu
color_scheme_menu = tk.Menu(context_menu, tearoff=0)
color_scheme_var = tk.StringVar(value=color_scheme)
for scheme in config['colors'].keys():
    color_scheme_menu.add_radiobutton(label=scheme.capitalize(), variable=color_scheme_var, value=scheme, command=lambda s=scheme: set_color_scheme(s))
context_menu.add_cascade(label=set_color_scheme_label, menu=color_scheme_menu)

# Add k8s context options to the context menu
k8s_context_menu = tk.Menu(context_menu, tearoff=0)
k8s_context_var = tk.StringVar(value=get_current_context())
context_menu.add_cascade(label=context_label_text, menu=k8s_context_menu)

update_context_menu()

# Start updating the context label
update_context_label()

# Run the main loop
root.mainloop()
