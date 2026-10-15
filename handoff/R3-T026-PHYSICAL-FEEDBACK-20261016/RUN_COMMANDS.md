# T026 actual run commands

Source: `5786e08575b19020a6aece338436406884d3542d`. Python 3.12 native Windows; no environment mixing. These are execution records, not permission to rerun or spend again. All output directories are immutable and must remain preserved.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONUTF8='1'
```

## A: compile original T025 draft (exit 0)

```powershell
& 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe' '-B' '-X' 'utf8' 'D:\OptoMind-Article-3\r3t026a\focused_research\route_stage.py' 'validate' '--task-sheet' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\routes\task_sheet.json' '--evidence-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T025\inputs\evidence_items.json' '--constraints-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\routes\design_constraints.json' '--routes-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T025\rejected-drafts\round-2.research_routes.json' '--output' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\compile'
```

## B: first real physical round (exit 0)

```powershell
& 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe' '-B' '-X' 'utf8' 'D:\OptoMind-Article-3\r3t026a\focused_research\scientific_stage.py' 'run' '--task-sheet' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\routes\task_sheet.json' '--evidence-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T025\inputs\evidence_items.json' '--constraints-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\routes\design_constraints.json' '--routes-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T025\rejected-drafts\round-2.research_routes.json' '--optical-media' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\inputs\optical_media.json' '--wavelengths' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\inputs\wavelengths_nm.json' '--cache-dir' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\cache' '--max-new-evals' '14' '--max-workers' '2' '--execute' '--output' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\round1'
```

## One zero-evaluation cache probe (exit 0)

```powershell
& 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe' '-B' '-X' 'utf8' 'D:\OptoMind-Article-3\r3t026a\focused_research\scientific_stage.py' 'run' '--task-sheet' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\routes\task_sheet.json' '--evidence-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T025\inputs\evidence_items.json' '--constraints-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\routes\design_constraints.json' '--routes-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T025\rejected-drafts\round-2.research_routes.json' '--optical-media' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\inputs\optical_media.json' '--wavelengths' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\inputs\wavelengths_nm.json' '--cache-dir' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\cache' '--max-new-evals' '0' '--max-workers' '2' '--execute' '--output' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\cacheprobe'
```

## Native metering preflight; no HTTP (exit 0)

```powershell
& 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe' '-B' '-X' 'utf8' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\launch_revision.py' 'preflight'
```

## C: one observation-fed revision (exit 0; one actual HTTP)

```powershell
& 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe' '-B' '-X' 'utf8' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\launch_revision.py' 'revise' '--execute'
```

## D: second real physical round (exit 0)

```powershell
& 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe' '-B' '-X' 'utf8' 'D:\OptoMind-Article-3\r3t026a\focused_research\scientific_stage.py' 'run' '--task-sheet' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\routes\task_sheet.json' '--evidence-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T025\inputs\evidence_items.json' '--constraints-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\routes\design_constraints.json' '--routes-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\revision\research_routes.json' '--optical-media' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\inputs\optical_media.json' '--wavelengths' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\inputs\wavelengths_nm.json' '--cache-dir' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\cache' '--max-new-evals' '52' '--max-workers' '2' '--execute' '--output' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\round2'
```

The physical launcher `run_physical_stage.py` recorded each command, exit, timing and durable reservation count. Round1 reserved 12 new evaluations; cacheprobe reserved 0; round2 had cap 52 and reserved 5. Model revision used the unchanged existing native adapter/factory, one new T026 task scope on the existing ledger, no transport retry.
