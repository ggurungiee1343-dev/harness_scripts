from index_db import list_components
rows = list_components()
for r in rows:
    comp = r["component"]
    status = r["status"]
    last = r["last_checked"]
    notes = r["notes"]
    print(f"{comp:30s} | {status:10s} | {last:22s} | {notes}")
