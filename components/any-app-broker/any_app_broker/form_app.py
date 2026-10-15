#!/usr/bin/env python3
"""Small deterministic Tk smoke app; stores only to a caller-owned temp path."""
import argparse
import json
import os
import tkinter as tk
from tkinter import messagebox

p = argparse.ArgumentParser()
p.add_argument('--state-file', required=True)
a = p.parse_args()
root = tk.Tk()
root.title('Orbit Form Smoke')
root.geometry('640x360+40+40')
root.resizable(False, False)
root.configure(padx=32, pady=24)
value = tk.StringVar()
status = tk.StringVar(value='Not saved')

def write_state(saved):
    with open(a.state_file, 'w', encoding='utf-8') as f:
        json.dump({'name': value.get(), 'saved': saved}, f)

def save():
    text = value.get()
    write_state(True)
    status.set('Saved: ' + text)

def reset():
    value.set('')
    status.set('Not saved')
    write_state(False)

def about():
    messagebox.showinfo('About Orbit Form Smoke', 'Disposable local smoke-test app.', parent=root)

menu = tk.Menu(root)
file_menu = tk.Menu(menu, tearoff=False)
file_menu.add_command(label='Reset', command=reset)
file_menu.add_separator()
file_menu.add_command(label='Quit', command=root.destroy)
menu.add_cascade(label='File', menu=file_menu)
help_menu = tk.Menu(menu, tearoff=False)
help_menu.add_command(label='About', command=about)
menu.add_cascade(label='Help', menu=help_menu)
root.config(menu=menu)
tk.Label(root, text='Contact name', anchor='w').place(x=8, y=24, width=140, height=28)
entry = tk.Entry(root, textvariable=value, name='name_entry')
entry.place(x=8, y=56, width=440, height=34)
tk.Button(root, text='Save', command=save, name='save_button').place(x=8, y=108, width=100, height=38)
tk.Label(root, textvariable=status, anchor='w', name='status_label').place(x=8, y=166, width=520, height=32)
root.mainloop()
