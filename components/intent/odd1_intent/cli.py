"""odd1-intent: state a file intent, review the exact plan, approve selected actions, verify, undo."""
import argparse, sys
from .engine import parse, apply, undo, IntentError

def show(p):
    print("PLAN (nothing changed):",p.intent)
    for i,a in enumerate(p.actions):
        mark="YES" if a.selected else "no "
        print(f"  [{mark}] {a.kind:7} {a.source} -> {a.target} | {a.reason}")
    if not p.actions: print("  No source files found; no actions.")

def main():
    ap=argparse.ArgumentParser(prog="odd1-intent",description="Intent — preview, approve, verify, undo")
    ap.add_argument("intent",nargs="*"); ap.add_argument("--undo",nargs="?",const="latest",help="undo latest transaction, or given transaction ID")
    args=ap.parse_args()
    try:
        if args.undo is not None:
            e=undo(None if args.undo=="latest" else args.undo); print("UNDONE",e["id"],"— files restored/removed and verified"); return 0
        if not args.intent: ap.error("provide an intent or --undo")
        p=parse(" ".join(args.intent)); show(p)
        if not p.actions or not any(a.kind in ("ADD","REPLACE") for a in p.actions): print("No changes to approve."); return 0
        default=','.join(str(i+1) for i,a in enumerate(p.actions) if a.selected)
        try:
            choice=input(f"Action numbers to approve (comma-separated; Enter keeps defaults [{default}], 'all' selects all ADD/REPLACE, 'none' cancels): ").strip()
        except EOFError:
            print("Cancelled; input ended before approval. Nothing changed.")
            return 1
        if choice.casefold()=='none': print("Cancelled; nothing changed."); return 1
        if choice.casefold()=='all': indices=[i for i,a in enumerate(p.actions) if a.kind in ('ADD','REPLACE')]
        elif choice:
            try: indices=sorted(set(int(x.strip())-1 for x in choice.split(',')))
            except ValueError: print("Invalid action number; nothing changed.",file=sys.stderr); return 2
        else: indices=[int(x)-1 for x in default.split(',') if x]
        if not indices: print("Cancelled; nothing changed."); return 1
        if any(i<0 or i>=len(p.actions) or p.actions[i].kind not in ('ADD','REPLACE') for i in indices): print("Selection includes a non-action or invalid number; nothing changed.",file=sys.stderr); return 2
        print("ABOUT TO APPLY:")
        for i in indices: print(f"  {p.actions[i].kind}: {p.actions[i].source} -> {p.actions[i].target}")
        try:
            answer=input("Apply exactly these actions? Type YES: ")
        except EOFError:
            print("Cancelled; input ended before final approval. Nothing changed.")
            return 1
        if answer!="YES": print("Cancelled; nothing changed."); return 1
        e=apply(p,indices); print(f"APPLIED {len(e['records'])} action(s); SHA-256 verified. Undo with: odd1-intent --undo {e['id']}"); return 0
    except (IntentError,OSError,ValueError) as ex: print("ERROR:",ex,file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
