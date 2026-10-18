import sys
sys.stdout.reconfigure(encoding="utf-8")
import sqlite3

conn = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "data/test_supplier.db")
conn.row_factory = sqlite3.Row

print("== claims ==")
for r in conn.execute("SELECT draft_id,status,unreachable FROM claims ORDER BY draft_id"):
    print(r["draft_id"], r["status"], "UNREACH" if r["unreachable"] else "")

print("== transitions ==")
for r in conn.execute("SELECT claim_id,old_status,new_status,cause,round FROM claim_transitions ORDER BY id"):
    print(r["claim_id"].split(":")[-1], r["old_status"], "->", r["new_status"],
          r["cause"] or "-", "R" + str(r["round"]))

print("== sections ==")
for r in conn.execute("SELECT title,state,snapshot_version,last_draft_round FROM sections ORDER BY ord"):
    print(r["title"], r["state"], "v" + str(r["snapshot_version"]), "R" + str(r["last_draft_round"]))

print("== degraded decisions ==")
for r in conn.execute("SELECT round,kind FROM decision_log WHERE degraded=1"):
    print("R" + str(r["round"]), r["kind"])

print("== skipped verdicts ==")
for r in conn.execute("SELECT round,payload_json FROM decision_log WHERE kind='判决'"):
    if '"skipped": true' in r["payload_json"]:
        print("R" + str(r["round"]), r["payload_json"][:220])

print("== llm retry hints(意见 errors) ==")
for r in conn.execute("SELECT round,payload_json FROM decision_log WHERE kind='其他'"):
    print("R" + str(r["round"]), r["payload_json"][:120])
