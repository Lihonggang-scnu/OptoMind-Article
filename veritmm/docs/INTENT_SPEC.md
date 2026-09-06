# ScientificIntentSpec — the semantic IR and its compilation certificate

`tmm_engine.intent` is the controlled semantic seam between "what an agent or
researcher asks for" and "what a TMM task says".  The layer is shared
vocabulary: OptoMind's language-side compiler (O-10) produces IntentSpecs;
this repository's `compile_intent` consumes them and emits tasks plus a
machine-readable **compilation equivalence certificate**.  Future FDTD/RCWA
compilers can target the same IR.

## Positioning

```text
natural language / upstream agent intent
        │  (NL → IntentSpec extraction lives downstream, e.g. OptoMind O-10)
        ▼
IntentSpec  ──compile_intent(spec, stack)──▶  SimulationTask / OptimizationTask
        │                                        │
        └──▶ CompilationEquivalenceCertificate   └──▶ existing verification chain
```

## IntentSpec fields (strict, JSON Schema via `veritmm schema intent`)

- `intent_id`, `source_clause` — identity and the reference natural-language
  clause the intent was annotated from (hash recorded on the certificate).
- `observables[]` — `quantity` (R/T/A; `layer_absorption` reserved and
  currently rejected), `domain` (wavelength window with explicit units —
  `nm`, `um`, `µm`, `μm` — plus `angle_deg` and `polarization`), `reducer`
  (`mean`/`min`/`max`/`band_worst_case`), `relation` (`>=`/`<=`/`==`),
  `target`, `constraint_role` (`hard`/`soft`), `weight`.
- `design_variables[]` — `layer_thickness` variables with a `layer_selector`
  (indices) and unit-carrying `bounds_min`/`bounds_max`.
- `hard_constraints[]` — `layer_count_max`, `total_thickness_max`,
  `forbidden_materials`, `substrate` (a material name or `constant:<n>`).
- `verification_requirement` — `energy: independent|closure`,
  `cross_solver`, `reciprocity` (echoed on the certificate for the V-02/V-03
  acceptance layers to consume).
- `uncertainty` — optional free-form metadata (not compiled).

Validation is fail-closed: unknown quantities, reducers, relations, units, or
extra fields are typed rejections — never guesses.

## The compilation equivalence certificate

Every compilation emits `compilation-equivalence.v1`:

- `source_clause` / `source_clause_hash` — what was asked;
- `intent_fields` — the full canonical IR snapshot;
- `compiled_task_fields` — each intent path mapped to its task path and value
  (for example `observables[0] → targets[0]: R at_least 0.99 over [550, 650] nm`);
- `unit_conversions` — every non-nm input with from/to values and factor;
- `defaults_inserted` **and** `ambiguities` — defaults are allowed but every
  inserted default appears in *both* lists (an unspecified reducer, an
  open-ended band), so nothing is silently smoothed over;
- `semantic_status` — `equivalent` (clean), `ambiguous` (compiled, with
  reported defaults/ambiguities/downgrades), or `rejected` (typed
  `rejection.code`, e.g. `constraint_violated`, `quantity_unsupported`,
  `selector_unsupported`).

Direction semantics are pinned by the regression set: `>=` compiles to
`at_least`, `<=` to `at_most`, `==` to `match`; maximize saturates at 1.0 and
minimize bottoms at 0.0 when no explicit target is given.  The
direction-reversal case class (the historical "maximize reflectance compiled
to at_most 0" failure) has zero silent errors: every case compiles exactly as
annotated or is explicitly ambiguous.

## Case set

`tmm_engine/intent/cases.json` holds ≥ 50 semantic regression cases, each with
reference Chinese and English clauses, annotated IntentSpec key fields, and
expected compilation assertions, covering: unit conversion (×8), direction
(×8), high/low-index material descriptions (×6), hard-vs-soft and weights
(×6), multi-objective/multi-band (×6), angle/polarization phrasing (×6),
structural constraints (×5), and mandatory-ambiguity examples (×5).  The NL
text is reference material for downstream extraction; the compiler is tested
on the annotated IntentSpec.

## Writing cases

1. One `case_id` per semantic point; both `nl_en` and `nl_zh` phrasings.
2. Annotate the minimal IntentSpec key fields the NL determines — do not
   pre-fill what the speaker left open (that is what the ambiguity machinery
   records).
3. Assert only on stable surfaces: `semantic_status`, `task_kind`,
   `targets[i].constraint/target/angle_deg/polarization`,
   `conversions[i].to_value`, `ambiguity_fields`, `rejection_code`.
4. A case whose NL leaves something open must expect `ambiguous` with the
   field listed — that is the contract working, not a failure.
