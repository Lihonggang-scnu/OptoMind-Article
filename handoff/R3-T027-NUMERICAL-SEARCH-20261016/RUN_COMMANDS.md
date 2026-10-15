# T027 actual commands

Source: `b5f8a1dc2cfe8a203b74a742963bef7f9ac9a57b`. Existing Python 3.12 Windows tensor environment; all runs already completed. These commands record evidence, not authorization to repeat.

## Required Torch gate (exit 0; 102 passed, no skips)

```text
"D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe" -m pytest -q -p no:cacheprovider --basetemp D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T027\pytest-temp focused_research/tests/test_family_optimization.py focused_research/tests/test_design_archive.py focused_research/tests/test_physics_diagnostics.py focused_research/tests/test_search_stage.py focused_research/tests/test_execution_failure_diagnostics.py
```

## search (exit 2; saved partial coverage, not retried)

```powershell
& 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe' '-B' '-X' 'utf8' '-m' 'focused_research.search_stage' 'search' '--task-sheet' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\routes\task_sheet.json' '--evidence-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T025\inputs\evidence_items.json' '--constraints-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T022\runs\routes\design_constraints.json' '--routes-file' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T025\rejected-drafts\round-2.research_routes.json' '--optical-media' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\inputs\optical_media.json' '--wavelengths' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T026\inputs\wavelengths_nm.json' '--parameterization' 'family' '--seed' '42' '--cache-dir' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T027\cache' '--max-forward-evaluations' '4096' '--max-new-evals' '64' '--max-workers' '2' '--backend' 'torch' '--execute' '--output' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T027\search'
```

## refined (exit 2; saved partial coverage, not retried)

```powershell
& 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe' '-B' '-X' 'utf8' '-m' 'focused_research.search_stage' 'refine' '--previous-search' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T027\search' '--revision-dir' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T027\revision' '--seed' '43' '--cache-dir' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T027\cache' '--max-forward-evaluations' '4096' '--max-new-evals' '64' '--max-workers' '2' '--backend' 'torch' '--execute' '--output' 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T027\refined'
```

## One native-metered model decision (exit 0; one actual HTTP)

```powershell
& 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe' -B -X utf8 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T027\launch_decision.py' preflight
& 'D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe' -B -X utf8 'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T027\launch_decision.py' revise --execute
```

All stages used PYTHONDONTWRITEBYTECODE=1 and PYTHONUTF8=1. Search seeds42 and43 were fixed by HANDOFF; production allocates per-job seeds from those values. No retry, backend fallback, installation or additional stage.
