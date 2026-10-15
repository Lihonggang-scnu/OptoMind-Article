# OptoMind R3 GitHub fallback relay

Repository: Lihonggang-scnu/OptoMind-Article
Branch: codex/r3-handoff-relay

Read this file before inspecting cloud_to_local/. This branch is a transport channel; every executable task must separately specify immutable full implementation and base SHAs, HANDOFF, commands, fee cap, model HTTP cap and numerical cap. Branch HEAD is not an implementation baseline.

Canonical Drive root: https://drive.google.com/drive/folders/1LMnPCaU2lDkXEvbuBvfT1zivDjULFya4
Drive cloud_to_local: 1T2mDaIdlp-0eYWHpe9QGbHqksdgu5bwI
Drive local_to_cloud: 13HsfHmSvXzE7TLnZxFQdlMMWBh38RYH0
Drive receipts: 1arJ_fCQQGHOCgA7X2EXzVBOxs8FrrXRB

Use this relay when Drive read/write is blocked after bounded retries. Append immutable UTF-8 task/result/receipt files under the matching directories. Preserve original message_id and task_id across both channels. Check receipts and local persistent state before claiming. Publish and read back claimed before executing; unknown upload outcomes must be reconciled before retry. Completed/blocked tasks must not be rerun. T027 / C2L-R3-0040 is completed. A cloud development claim is not a ready local work order.

Only normal pushes to this independent branch; never change main, force push, overwrite historical messages, or push the Review upstream. If the remote branch advances, fetch and compare/merge without overwriting others. Keep credentials, environment files, ledger databases, raw PDFs and model weights local. No ZIP uploads.

Local agent owns tests and simple wiring repairs. Cloud owns core algorithms, prompts and research-flow changes. Core defects require minimal reproduction and evidence, then blocked or a new cloud task. Do not change scientific answers, inputs, acceptance criteria, seeds or limits to obtain success. Preserve raw model responses, actions, feedback and revisions; distinguish facts, assumptions and refuted predictions. Execution completed does not imply scientific success.

Effective project cumulative cap is 120 CNY including prior spending. Read-only reconcile all existing project ledgers, settled/pending/held/uncertain and valid subpool limits before paid work. Old database cap metadata 140 does not grant authorization. Do not create new pools or release unknown holds. Require an effective 120 CNY outer gate before every paid call. No task means no testing or model calls; the 12-case goal and aim of three demonstrations do not authorize undeclared cases.

Work only in independent directories under D:/OptoMind-Article-3. F: mother projects are read-only. Preserve old versions, outputs, caches and uncommitted changes. Never duplicate an active task or interrupt it just because a 30-minute check fires.

When Drive recovers, mirror missing immutable messages/receipts with the same IDs, verify readback and update cross-channel state; never execute a task again during synchronization.
