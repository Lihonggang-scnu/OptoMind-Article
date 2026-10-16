# T034 case02 P002 attribution correction

This is an additive correction; the original T034 quality report remains byte-preserved.

The immutable model-facing REQUEST compact P002 row reports dL for G2 only with missing G1 and dC2 for G1 only with missing G2. The user JSON and wire_body copies are byte-identical (c80f86a79645f5c2d9c1ba6986d8dc39fd9607b4fd2e822fd664ef0fe5d1b64f).

The authoritative saved numerical-search row is evaluation 50, parent evaluation 24, design design:0dfa0da6805d956226e86249295b9c269130a4ed0247971be4bbc9df5382a2ea, with a complete normalized Jacobian for dH,dL,dC1,dC2. The prior T034 criticism specifically concerning the P002 dL derivative was a source-attribution mistake: it compared compact model-facing dL (G2 only) with the fuller saved Jacobian containing dL G1. The dC2 missing-G2 field is separately a verified projection gap; this correction does not claim that the old report classified that omission as a model error. Both compact omissions remain unknown legacy values.

The saved dL G1 value is 0.0005033902132708982 per normalized coordinate over a 20 nm span, or 0.00002516951066354491 per nm. The saved dC2 G2 value is -0.003215842098587008 per normalized coordinate over a 50 nm span, or -0.00006431684197174016 per nm. Normalized values are not per-nm values.

P012 derivative sign/band findings and the P003 stable-ID versus narrative-identity finding are unaffected.

This replay adds no observations, forward rows, solver or physics result, certification, retrieval, model call, or cost.
