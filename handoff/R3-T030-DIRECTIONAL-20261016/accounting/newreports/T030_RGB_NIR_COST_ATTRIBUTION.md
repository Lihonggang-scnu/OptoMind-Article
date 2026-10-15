# T030 RGB/NIR historical cost attribution

Read-only audit for the 8 CNY aggregate cap. No model call, network request, test run, ledger write, or ledger copy was performed.

## Result

The source-supported, reservation-identity-backed RGB/NIR spend is **1.7310734 CNY**:

| Source | Rows | Actual CNY | Evidence |
|---|---:|---:|---|
| Original ledger, `R3-T010-e2e-11-rgb-nir-splitters` | 16 | 0.7516198 | T010 `REPORT.md` and `CASE_SUMMARY.json`; task-linked rows |
| Dedicated pool, T019 → T022 → T025 → T026 → T027 → T028 | 10 | 0.9794536 | Existing task reports and pool task-linked rows |
| **Identity-backed RGB/NIR subtotal** | **26** | **1.7310734** | Disjoint reservation IDs; summed once |

The exact reservation and call identities are in [`T030_RGB_NIR_COST_ATTRIBUTION.json`](./T030_RGB_NIR_COST_ATTRIBUTION.json). Its `all_reservation_ids_at_audit` contains all 338 rows from both scoped ledgers; the RGB sections contain the 26 case rows.

## Unlinked original history

The original ledger has **258 unlinked rows totaling 15.2235752 CNY**. Directory identity and sanitized reports separate 61 local-functional-trial rows (0.4619124 CNY) and 75 R2-current rows (7.4954824 CNY); those reports describe the Article two-layer AR and Review metalens lanes and explicitly report no new RGB/NIR case.

The remaining **122 rows totaling 7.2661804 CNY** have no row-level task binding in the original ledger and no bounded sanitized output that binds each identity to a case. They remain **unresolved**. They must not be assigned to RGB/NIR by prefix, chronology, or static replay similarity. Their full identity list is under `case_groups.original_unlinked_residual_122.identities` in the JSON.

The static replay summary for `e2e-11-rgb-nir-splitters` reports an estimated 4.6599052 CNY and 72 Qwen calls, but it contains no reservation or call identity matching either scoped ledger. It is recorded as out-of-ledger evidence and is not added to the ledger-backed cap.

For visibility only, the mechanical sum of that estimate plus the identity-backed subtotal is 6.3909786 CNY (1.6090214 CNY below the cap) **if** the two provenance sets were independent. They cannot be added as an authoritative historical total because no identity reconciliation proves that the static replay estimate is disjoint from the unresolved original history; this prevents double counting.

## Cap decision

`attribution_complete=false`. Known RGB/NIR headroom is **6.2689266 CNY**. If the 122-row residual is conservatively allowed to be RGB/NIR, the RGB/NIR upper bound is **8.9972538 CNY**, already above the 8 CNY cap. If every unlinked row is left unresolved, the upper bound is **16.9546486 CNY**. The aggregate cap is therefore **not safe to spend** until the residual identity attribution is resolved.

Both ledgers were fully settled at audit: original 322 rows / 17.329267 CNY and pool 16 rows / 1.088912 CNY. Their audit SHA-256 values and all identities are recorded in the JSON. No expense is double-counted: original T010 rows and pool continuation rows are in separate ledgers and have disjoint identities.

