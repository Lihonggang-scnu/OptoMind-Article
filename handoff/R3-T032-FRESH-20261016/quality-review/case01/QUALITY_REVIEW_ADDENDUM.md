# T032 case01 quality-review addendum

Source SHA: `8a38b2d0d7b18c4308c4f5978691a234f6ab3732`.

## Model-facing derivative checks

The audit used the saved revision REQUEST component rows only. The request exposes both explicitly `derivative_per_nm` fields and separate normalized-coordinate rates; normalized rates are not per-nm quantities. Bound-only epsilon is not a recommended step or validity radius.

| Parent | Stable ID | dH sign for decrease (G1/G2) | dL sign for increase (G1/G2) | Plan expectation | Certified children | Result |
|---|---|---|---|---|---:|---|
| P027 | `design:e9be80aaea80e6d3161f787315678ce2db6d972907241ed2efeb27711bf179a7` | improves / improves | improves / improves | increase / increase | 0 | supported_as_local_first_order_prompt_claim |
| P023 | `design:c4bbca735d29226605d06282cd327a7152b766c47ace2ea4add9686fb378a926` | improves / improves | improves / worsens | increase / unchanged | 0 | supported_as_local_first_order_prompt_claim |
| P018 | `design:ac974ff506d5876859570f008ae16ad8b5236a3395ccd5b6462584fa9007a52b` | improves / improves | improves / worsens | increase / increase | 0 | supported_as_local_first_order_prompt_claim |
| P006 | `design:2a5ffc067851bcc430064178769ef1f5230ad940eb4f37bba8e0cfc4a34d3e24` | improves / worsens | improves / worsens | increase / increase | 32 | prompt components support a dL tradeoff and do not certify the later layerwise finite-step result |

For P027, the actual request components are dH derivatives G1/G2 = -0.0002516157303796095 / -0.004433735090843612 per nm and dL derivatives = 0.00044616642522876654 / 0.0006375894649401736 per nm; its prompt joint direction is dH=-1, dL=0.8087375867546296 with positive first-order rates for both goals. The decrease-dH/increase-dL joint-ascent statement is therefore sign-supported locally, while remaining uncertified and untested at finite step.

For P023, dH derivatives are -0.0004599341320800761 / -1.5030757401875887e-05 per nm, so decreasing dH locally improves both maximize goals. For P018, they are -0.000522710701565055 / -0.0031425675247028057 per nm, giving the same local sign. Neither parent has a certified child; these expectations are untested, not refuted.

## Route rejection causes

- `model_unknown_evidence_id`: D5 — research routes decision_clue.evidence_id: unknown evidence ID. This is a saved model-content or route-contract guard outcome, not a physics failure or identified core bug.
- `route_parameter_out_of_family_range`: R2, R3, R4, R8, R10 — research routes /routes/1/instances: thickness parameter outside family range. This is a saved model-content or route-contract guard outcome, not a physics failure or identified core bug.
- `dependent_route_invalid_due_to_D5`: R5, R5_Revised, R6 — research routes /routes/4/clue_ids: invalid clue dependencies: D5. This is a saved model-content or route-contract guard outcome, not a physics failure or identified core bug.
- `basis_mode_external_citation_contract_mismatch`: R7 — research routes route.basis_mode: model_knowledge cannot claim external citations; select hybrid to cite evidence. This is a saved model-content or route-contract guard outcome, not a physics failure or identified core bug.

## First three Q1 retrieval records

The following are the first three saved Q1 evidence rows, assessed directly from title/snippet fields:

- `E_1a57cc1a35727685e9f15f16fcce3632d8444f2ff9d9e4d7c2220202e67e8ee8` — **Multilayer optical filter for energy-efficient smart-window applications.** (abstract): Architecture analogy only: cascaded dielectric/TMM context is useful, but the text uses SiOF/Si3N4/BaSnO3 and describes visible transmission with NIR rejection, not the HfO2/SiO2 SWIR-transmission target.
- `E_ad2c17c62fff78d2689a8ba65de543698e62e1036bf61b2dbf828391435c5648` — **Multilayer optical filter for energy-efficient smart-window applications.** (title): Duplicate title-only record; insufficient technical evidence by itself.
- `E_17796fd0bc9c97e41260ef432e8c88c9c2d29aea02f644bc5ffca20797054a0d` — **ANALYSIS OF CURRENT AND FUTURE DEMAND FOR BROADBAND ACCESS IN CANTÓN ANTONIO ANTE AND PROPOSAL FOR THE DESIGN OF A PASSIVE OPTICAL NETWORK ( PON ) FOR THE COMPANY CNT E . P . AS A SOLUTION TO FUTURE DEMANDS** (title): Title-only record unrelated to multilayer methane-window design; not useful for the stated coating decision.

No tests, model calls, network calls, or physics runs were made for this addendum.
