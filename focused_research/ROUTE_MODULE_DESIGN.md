# Evidence-guided candidate routes — provisional first implementation

## Scope and ownership

This module extends the isolated focused-research worktree based on `713799fb55899b78427aed56cb643bc72526c19d`. The previous task-sheet version remains fixed for the ongoing T019 local test. Cloud owns design and implementation; local testing and minor environment fixes do not authorize changing the scientific design.

This stage ends at **retrieved evidence → decision clues → proposed, parameterized research routes**. It does not perform optical simulation, optimize a device, validate a mechanism, allocate experimental resources, or generate a PDF. `ready` describes a prepared contract, never a successful experiment or a verified scientific explanation.

## 1. Deterministic, lightweight retrieval

Execute the task sheet's exact endpoint-specific queries. Default to 10 hits per query, configurable to 5, 15 or 20 and bounded by the existing provider cap. These values are starting points for later relevance tuning, not an empirically optimal depth. No model is involved in retrieval execution, deduplication or storage.

Keep the original retrieved text, its type (title, abstract, body or unknown), source identity/URL, original query and associated goal IDs. Missing author, year or DOI is acceptable. Missing text or a usable source locator is not usable scientific evidence; retain such records for diagnostics without inventing contents. Never label every S2 snippet as body text. The snippet service does not replace figures, captions or full-paper inspection.

Deduplicate evidence without discarding the multiple queries/goals that found it. Distinct passages from the same paper remain distinct. Preserve raw public provider records separately; never persist request headers, secrets or the user's billing database. No automatic PDF parsing or metadata-completion pipeline.

## 2. Decision clues, not paper summaries

A clue distinguishes an exact source excerpt from the model's interpretation and transfer assumptions. It states the structure/method/effect, known and unknown conditions, relevant subgoals and the inference needed to use it for this task. Weak evidence is admissible; fabricated support is not. An exact excerpt proves text correspondence, not scientific validity or that the interpretation follows logically.

Candidate generation may adapt an analogous device, transfer a method, combine clues across papers, or extrapolate cautiously. No fixed proportions or claim of novelty is imposed. A combination must explain compatibility assumptions instead of treating independently successful parts as automatically compatible.

## 3. Separate structure, device instance and explanation

A parameterized family describes material ordering and adjustable thicknesses with explicit bounds. Concrete parameter settings instantiate devices. The explanation names the supporting clues, transfer assumptions and expected directional effects. Multiple explanations may support a family, and a useful device can survive a later rejection of its explanation.

This first implementation uses explicit layer sequences with bounded named thickness variables and finite seed configurations. This is a general, deterministic representation for planar stacks, not unrestricted model-generated Python. New family generators can be added after assessing actual route outputs. An unsupported or underspecified proposal remains `needs_revision` and cannot be materialized for downstream computation.

The existing task sheet stores some structural constraints in natural language. For this version, pass an explicit, source-linked design-constraints configuration. It must be reviewed against the task; do not infer that any arbitrary configuration has been authorized simply because its JSON parses. Unspecified physical inputs, such as incident medium or material datasets, require explicit configuration/assumptions. Keep task-defined wavelength bands, angle, polarization and averaging convention unchanged.

## 4. Pre-execution expectations

Each expectation identifies a reference, a proposed change, controlled conditions, an existing task goal, a direction of change and its reason. A reason may be a literature inference or an explicitly exploratory hypothesis. Do not invent precise percentages, observed results or calibrated probabilities.

Freeze the proposal and inputs before future simulation. A later implementation should compare observations with the frozen expectation and report support, contradiction or insufficient evidence separately from performance. Equal-budget comparisons are needed when comparing optimized candidates. A poor starting point is not a rejection of its entire family; a matching response is not proof of a unique mechanism.

## 5. Future extensions, not implemented acceptance claims

- Common low-cost probes and appropriately normalized intervention responses for diversity preservation.
- Separate retention of high-performing devices and revision of their explanations.
- Initial comparable search budgets, additional resources based on observed progress, and retained exploration.
- Relevance tuning of actual Qwen-generated queries, depth comparisons (5/10/15/20), and weak-evidence usefulness.
- Equal-budget ablations to establish whether literature guidance and expectation checks improve decisions.

Response diversity does not itself establish different mechanisms. Different layer counts require comparable probes rather than direct comparison of incompatible raw gradient vectors.

## Evidence and testing boundaries

Offline tests exercise parsing, source/goal linkage, missing metadata, exact excerpt anchoring, constraints, deterministic materialization and transport failure handling. Synthetic fixtures must be explicitly labeled; they establish no actual literature retrieval quality, route novelty or optical performance. Live Qwen/S2 testing is performed only in the user's original local configuration and billing controls under a separately released task cap. This cloud implementation must not use project paid keys.
