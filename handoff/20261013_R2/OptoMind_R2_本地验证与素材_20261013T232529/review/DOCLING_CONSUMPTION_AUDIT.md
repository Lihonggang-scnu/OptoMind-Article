# Docling consumption audit

Status: **passed**

This is a read-only audit of saved R2 directed-reading requests. It used saved Docling `document.json` files and existing snapshots/formula receipts; it made no model call, PDF parse, network request, or source/old-artifact write.

## Evidence chain

- BATCH_RESULT: `D:\OptoMind-Article-3\local-functional-trial\20261013\review\docling-basic\BATCH_RESULT.json`
- Batch status: `success`, records: `2`, unique PDFs: `2`
- Directed-reading requests audited: `7`
- Tokenizer correction: `F:\OptoMind-Review-2\data\tokenizers\qwen3_5_9b\tokenizer.json` exists with `12807982` bytes; the earlier missing-path claim is retracted.

## Document bindings

| Paper | Batch label | document.json SHA | Source body length/SHA | Formula regions |
|---|---|---|---:|---:|
| Bandwidth bounds for wide-field-of-view dispersion-engineered achromatic metalenses | `60004cedb73ee78a6aa2` | `caaf0d6f9a5754607fe40f76885678eb2aba77b06f823b923ca380a1abe1b3e3` | `36637` / `4cc2d8dd4de5bf2dfbc7caae80dc95eb24460d209f8929ecfb83bd4324c37c78` | `6` |
  - document: `D:\OptoMind-Article-3\local-functional-trial\20261013\review\docling-basic\60004cedb73ee78a6aa2\document.json`; bytes/hash match BATCH_RESULT: `True`
  - snapshot pdf_layout_raw: `D:\OptoMind-Article-3\local-functional-trial\20261013\review\materials-02\84c74988ea8a29fd3a5a26d2655fc7505820f72e\snapshot-8645357434f7f7dd690428bf\sources\docling-layout-677de26a944fc31a-pdf_layout_raw.json`; raw SHA match: `True`
  - formula material: `D:\OptoMind-Article-3\local-r2-campaign\20261013_R2\review\formula-cache\90b70b62eaaf22c2c01cb7afccf5b503b87638d4ee6ba55601ac9449a3fd18c5\LOCAL_FORMULAS.json`; enhanced document exists/hash match: `True` / `True`
  - formula pointers: `[('d00031', '#/texts/25'), ('d00039', '#/texts/32'), ('d00041', '#/texts/34'), ('d00043', '#/texts/36'), ('d00057', '#/texts/49'), ('d00061', '#/texts/53')]`
| Metalenses at visible wavelengths: past, present, perspectives | `e3e00b4db932b9f95225` | `369b0c226b6db8f32458f43e9dda5a97db6b93915c75fd553c37bd3a686e7d52` | `63332` / `db7379393b197f67ba66dd8a522ea78324ed955b008b95fb8c0bdc2a308ce709` | `1` |
  - document: `D:\OptoMind-Article-3\local-functional-trial\20261013\review\docling-basic\e3e00b4db932b9f95225\document.json`; bytes/hash match BATCH_RESULT: `True`
  - snapshot pdf_layout_raw: `D:\OptoMind-Article-3\local-functional-trial\20261013\review\materials-02\dd2dc3ec9c62d3fea69db49d6c9a4c78eb1a6f49\snapshot-08c038bb8496aee2485270fa\sources\docling-layout-76aa672aa3ce8391-pdf_layout_raw.json`; raw SHA match: `True`
  - formula material: `D:\OptoMind-Article-3\local-r2-campaign\20261013_R2\review\formula-cache\710e83193d140696c411ea9a4b0bb0059bfff95048d699152e46b9f58993c9cd\LOCAL_FORMULAS.json`; enhanced document exists/hash match: `True` / `True`
  - formula pointers: `[('d00191', '#/texts/98')]`

## Reader receipts

| Call directory | Paper | production_call_id | provider call/request | body equality | structured equality | native marker binding |
|---|---|---|---|---:|---:|---:|
| `review-004f4731de4b45b6` | Bandwidth bounds for wide-field-of-view dispersion-engineered achromatic metalenses | `directed-reading:visible-achromatic-metalens-pilot-20261013:dr-task-c06cf7970667225e6e9b8aa2:attempt-01` | `review-004f4731de4b45b6` / `chatcmpl-458d53c0-51d0-9ca7-8099-6a68b29baad2` | `True` | `True` | `True` |
| `review-13b4262275244737` | Metalenses at visible wavelengths: past, present, perspectives | `directed-reading:visible-achromatic-metalens-pilot-20261013:dr-task-bd6ab8480da1e9f11abf37ae:attempt-01` | `review-13b4262275244737` / `chatcmpl-1a0fdcac-b682-937f-98de-24c3cf9de8d0` | `True` | `True` | `True` |
| `review-53feb43cb1164977` | Metalenses at visible wavelengths: past, present, perspectives | `directed-reading:visible-achromatic-metalens-pilot-20261013:dr-task-9ffa40b225869ed2c19d744a:attempt-01` | `review-53feb43cb1164977` / `chatcmpl-986847cc-006a-9359-8c15-561940204c12` | `True` | `True` | `True` |
| `review-b4d2ea57346e477f` | Bandwidth bounds for wide-field-of-view dispersion-engineered achromatic metalenses | `directed-reading:visible-achromatic-metalens-pilot-20261013:dr-task-d054c955c19271bf7be34eae:attempt-01` | `review-b4d2ea57346e477f` / `chatcmpl-3cee8160-fef6-96b5-bcca-80f60c70ac64` | `True` | `True` | `True` |
| `review-bd4932aaa29c488e` | Metalenses at visible wavelengths: past, present, perspectives | `directed-reading:visible-achromatic-metalens-pilot-20261013:dr-task-037523e46712227ed8da5eff:attempt-01` | `review-bd4932aaa29c488e` / `chatcmpl-05388ee4-dd61-9e95-8992-da9afd6d5607` | `True` | `True` | `True` |
| `review-d111ef3ff9264104` | Metalenses at visible wavelengths: past, present, perspectives | `directed-reading:visible-achromatic-metalens-pilot-20261013:dr-task-7e27bf8130f9414bc31827bb:attempt-01` | `review-d111ef3ff9264104` / `chatcmpl-2bcb686b-7ca0-97b6-84dc-28aea3f7c311` | `True` | `True` | `True` |
| `review-d1e53f56c6bf4405` | Bandwidth bounds for wide-field-of-view dispersion-engineered achromatic metalenses | `directed-reading:visible-achromatic-metalens-pilot-20261013:dr-task-e28c9b2414ed4bf16dc3729e:attempt-01` | `review-d1e53f56c6bf4405` / `chatcmpl-84e9162f-74e4-9653-942a-e53f66f68f2a` | `True` | `True` | `True` |

## Interpretation and limits

- Every audited request's `source_material` exactly equals the prepared body projection whose `pdf_layout_raw` bytes/hash equal one of the two D-side Docling batch `document.json` files.
- Every audited request's `structured_source_material` exactly equals the existing local formula projection. Formula region IDs and their `#/texts/...` pointers are bound to native Docling regions; the plain request string itself contains region IDs, while the pointers live in `LOCAL_FORMULAS.json`.
- The audit does not establish provider answer quality or scientific truth.
