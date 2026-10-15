"""Intent desktop window (Tk): request, preview, select, approve, history and undo over the shared engine."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from .engine import parse, apply, undo, history, IntentError
from .gui_logic import handle_parse_failure, refresh_approval_state, clear_completed_preview, clear_failed_preview, invalidate_after_input_change

class App(tk.Tk):
 def __init__(self):
  super().__init__(); self.title('Intent — tell the computer what to change'); self.geometry('1000x720'); self.minsize(760,560)
  self.plan=None; self.vars=[]
  ttk.Label(self,text='Say what you want. Review the exact file changes before approving.',font=('Sans',12,'bold')).pack(anchor='w',padx=8,pady=(2,2))
  row=ttk.Frame(self); row.pack(fill='x',padx=12)
  self.intent=tk.StringVar(value='Update the files in my project folder with the ones from Downloads')
  ttk.Entry(row,textvariable=self.intent,font=('Sans',12)).pack(fill='x',expand=True,side='left'); ttk.Button(row,text='Plan request',command=self.plan_request).pack(side='left',padx=5)
  paths=ttk.LabelFrame(self,text='For folder updates, identify the two folders (the request itself stays yours)'); paths.pack(fill='x',padx=16,pady=10)
  self.dst=tk.StringVar(value=str(Path.home()/'projects/orbit-demo')); self.src=tk.StringVar(value=str(Path.home()/'Downloads/orbit-demo'))
  self.pathrow(paths,'Project / destination',self.dst); self.pathrow(paths,'Downloads / source',self.src)
  buttons=ttk.Frame(self); buttons.pack(fill='x',padx=16,pady=(0,8))
  ttk.Button(buttons,text='Preview exact actions',command=self.plan_request).pack(side='left')
  self.approve=ttk.Button(buttons,text='Approve selected changes',command=self.approve_plan,state='disabled'); self.approve.pack(side='left',padx=8)
  ttk.Button(buttons,text='Undo latest',command=self.undo_latest).pack(side='left')
  ttk.Button(buttons,text='Refresh history',command=self.refresh_history).pack(side='left',padx=8)
  self.state=tk.StringVar(value='Ready — planning does not change any files.')
  # Any edit to intent or folder controls makes the displayed plan stale. Do not
  # leave an old approval actionable while the operator is composing a request.
  for variable in (self.intent,self.dst,self.src):
   variable.trace_add('write', self.inputs_changed)
  ttk.Label(self,textvariable=self.state,foreground='#174c24',wraplength=950).pack(fill='x',padx=12,pady=3)
  frame=ttk.LabelFrame(self,text='Plan — exact source, target, and decision. Uncheck anything you do not approve.'); frame.pack(fill='both',expand=True,padx=12,pady=2)
  self.canvas=tk.Canvas(frame,highlightthickness=0); self.scroll=ttk.Scrollbar(frame,orient='vertical',command=self.canvas.yview); self.rows=ttk.Frame(self.canvas)
  self.rows.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
  self.canvas.create_window((0,0),window=self.rows,anchor='nw'); self.canvas.configure(yscrollcommand=self.scroll.set); self.canvas.pack(side='left',fill='both',expand=True); self.scroll.pack(side='right',fill='y')
  hist=ttk.LabelFrame(self,text='Recent actions and undo IDs'); hist.pack(fill='x',padx=16,pady=7)
  self.hist=tk.Listbox(hist,height=4); self.hist.pack(fill='x',padx=4,pady=4); self.refresh_history()
 def pathrow(self,parent,label,var):
  r=ttk.Frame(parent); r.pack(fill='x',padx=6,pady=3); ttk.Label(r,text=label,width=25).pack(side='left'); ttk.Entry(r,textvariable=var).pack(side='left',fill='x',expand=True)
  ttk.Button(r,text='Browse…',command=lambda v=var:self.browse(v)).pack(side='left',padx=5)
 def browse(self,var):
  p=filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
  if p: var.set(p)
 def plan_request(self):
  text=self.intent.get().strip()
  if 'update' in text.casefold() and ('project folder' in text.casefold() or 'my project' in text.casefold()):
   text=f'Update files in "{self.dst.get()}" with the ones from "{self.src.get()}"'
  try: self.plan=parse(text)
  except Exception as e:
   # Invalidate every part of the prior preview: never leave an old approval
   # actionable (or visible as though it describes the latest request).
   handle_parse_failure(self,e,messagebox.showerror); return
  for w in self.rows.winfo_children():w.destroy()
  self.vars=[]
  if not self.plan.actions: ttk.Label(self.rows,text='No source files to change. No action will be taken.').grid(sticky='w'); self.approve.configure(state='disabled')
  for i,a in enumerate(self.plan.actions):
   v=tk.BooleanVar(value=a.selected); self.vars.append(v)
   line=ttk.Frame(self.rows); line.grid(row=i*2,column=0,columnspan=2,sticky='ew',padx=4,pady=4); line.columnconfigure(1,weight=1)
   ttk.Checkbutton(line,variable=v,command=self.refresh_approval_state).grid(row=0,column=0,sticky='nw',padx=4)
   details=ttk.Frame(line); details.grid(row=0,column=1,sticky='ew')
   ttk.Label(details,text=f'{a.kind} — {a.reason}',font=('Sans',10,'bold')).pack(anchor='w')
   ttk.Label(details,text='FROM  '+a.source,wraplength=780,justify='left').pack(anchor='w')
   ttk.Label(details,text='TO       '+a.target,wraplength=780,justify='left').pack(anchor='w')
   ttk.Separator(self.rows,orient='horizontal').grid(row=i*2+1,column=0,columnspan=2,sticky='ew')
  selected=sum(a.selected for a in self.plan.actions)
  self.approve.configure(state='normal' if selected else 'disabled'); self.state.set(f'PREVIEW ONLY: {len(self.plan.actions)} file(s) evaluated, {selected} selected. Nothing has changed. Select/unselect actions, then approve.')
 def inputs_changed(self,*_):
  invalidate_after_input_change(self)
 def refresh_approval_state(self):
  refresh_approval_state(self)
 def approve_plan(self):
  if not self.plan:return
  chosen=[i for i,v in enumerate(self.vars) if v.get()]
  if not chosen: messagebox.showinfo('No changes selected','Nothing will be changed.'); return
  lines=[f"{self.plan.actions[i].kind}: {self.plan.actions[i].source} → {self.plan.actions[i].target}" for i in chosen]
  if not messagebox.askyesno('Confirm exact actions','Proceed with only these selected file changes?\n\n'+'\n'.join(lines)): return
  try:
   e=apply(self.plan,chosen); clear_completed_preview(self); self.state.set(f"DONE: {len(e['records'])} action(s) copied and SHA-256 verified. Undo is available below (ID {e['id']}).")
   self.refresh_history(); messagebox.showinfo('Changes complete','Actions verified. Use Undo latest to restore the prior state.')
  except Exception as ex:
   # apply may fail after touching files if compensation was incomplete. Retire
   # this preview so it cannot be retried from stale assumptions; inspect the
   # error and state, then explicitly plan again.
   clear_failed_preview(self); messagebox.showerror('No complete operation',str(ex))
 def undo_latest(self):
  if not messagebox.askyesno('Undo latest','Restore replaced files from backups and remove files this operation added? Changes made afterward prevent undo; if detected, undo is refused. Continue?'):return
  try:e=undo(); self.state.set(f"UNDONE {e['id']}: replaced bytes restored and added files removed."); self.refresh_history(); messagebox.showinfo('Undo complete','The transaction was reversed.')
  except Exception as ex: messagebox.showerror('Undo refused',str(ex))
 def refresh_history(self):
  self.hist.delete(0,'end')
  try:
   for e in history():self.hist.insert('end',f"{'UNDONE' if e['undone'] else 'APPLIED'}  {e['id']}  {len(e['records'])} files  {e['intent']}")
  except Exception as ex:self.hist.insert('end',str(ex))
def main():
 App().mainloop()

if __name__=="__main__": main()
