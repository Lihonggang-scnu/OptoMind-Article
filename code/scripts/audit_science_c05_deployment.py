"""Offline deployment probe; outputs honest process/package and filesystem evidence."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from optomind_eval.process_boundary import EvaluatorProcess, export_policy_capsule
from optomind_eval.worlds import make_world
from optomind_eval.world_audits import write_sealed_audit

# A separate policy process imports only the exported existing WorldClient module.
POLICY = """
import sys,json,os
sys.path.insert(0,sys.argv[1])
from world_client import WorldClient
def transport(request):
 print(json.dumps({"request":request}),flush=True)
 return json.loads(sys.stdin.readline())
client=WorldClient(transport)
try:
 import optomind_eval
 evaluator_import_denied=False
except ImportError:
 evaluator_import_denied=True
# Report the actual host permission, without exporting file content.
try:
 with open(sys.argv[2],"rb") as handle: handle.read(1)
 hidden_file_read_denied=False
except PermissionError:
 hidden_file_read_denied=True
a=client.purchase("boundary","first","R",[500.,600.])
b=client.purchase("boundary","first","R",[500.,600.])
try:
 client.purchase("boundary","unauthorized","A",[500.,600.])
 channel_denied=False
except PermissionError:
 channel_denied=True
sealed=client.submit_terminal("boundary","done",{"film_n":2.,"thickness_nm":100.})
print(json.dumps({"result":{"policy_pid":os.getpid(),"policy_uid":os.getuid(),
 "evaluator_import_denied":evaluator_import_denied,"hidden_file_read_denied":hidden_file_read_denied,
 "idempotent":a==b,"unavailable_channel_denied":channel_denied,"terminal_fields":sorted(sealed),
 "purchased_cost":a.cost}}),flush=True)
"""


def run_probe(output_dir):
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=False)
    world = make_world("F0", task_id="boundary", split="V", seed=8265000)
    config = root / "evaluator-input.json"
    config.write_text(json.dumps({"worlds": [asdict(world)], "budget": 1.}))
    capsule = root / "policy-capsule"
    export_policy_capsule(capsule)
    transcript = []
    with EvaluatorProcess(config) as evaluator:
        policy = subprocess.Popen([sys.executable, "-I", "-c", POLICY, str(capsule.resolve()), str(config.resolve())],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            while True:
                line = policy.stdout.readline()
                if not line:
                    raise RuntimeError("policy exited without result: " + policy.stderr.read())
                item = json.loads(line)
                if "result" in item:
                    result = item["result"]
                    break
                response = evaluator.request(item["request"])
                transcript.append({"operation": item["request"]["operation"],
                                   "request_sha256": hashlib.sha256(json.dumps(item["request"], sort_keys=True).encode()).hexdigest(),
                                   "response_fields": sorted(response)})
                policy.stdin.write(json.dumps(response) + "\n")
                policy.stdin.flush()
            policy.stdin.close()
            if policy.wait(timeout=10):
                raise RuntimeError("policy failed")
        finally:
            if policy.poll() is None:
                policy.terminate()
                policy.wait(timeout=5)
            policy.stdout.close()
            policy.stderr.close()
        result.update({"evaluator_pid": evaluator.handshake["pid"], "evaluator_uid": evaluator.handshake["uid"],
                       "launcher_pid": os.getpid(), "distinct_processes": len({os.getpid(), result["policy_pid"], evaluator.handshake["pid"]}) == 3,
                       "os_security_isolation_established": False,
                       "security_gate": "pending external filesystem/principal deployment",
                       "scope": "actual subprocess and package export; same UID and host filesystem remain accessible",
                       "transcript": transcript})
    (root / "boundary-report.json").write_text(json.dumps(result, indent=2) + "\n")
    worlds = [make_world(f"F{i}", task_id=f"audit-F{i}", split="V", seed=8265100 + i) for i in range(9)]
    write_sealed_audit(worlds, root / "sealed-world-audits.json")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(run_probe(args.output_dir), indent=2))
