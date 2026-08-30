# 凭证配置

本目录只提供空的凭证文件模板。每个文本文件使用“一行一个密钥”的格式；凭证由运行者在本地安全填写，不进入源代码、运行日志或公开版本库。

当前 harness 使用：

- `qwen-api-key.txt`：Qwen 服务密钥；也可通过 `QWEN_API_KEY_FILE` 或 `DASHSCOPE_API_KEY_FILE` 指向其他安全位置。
- `semantic-scholar-api-key.txt`：Semantic Scholar 方法检索密钥；也可通过 `SEMANTIC_SCHOLAR_API_KEYS_FILE` 指向其他安全位置。

若启用 OpenAlex 检索，可在本地另行配置 `OPENALEX_API_KEYS_FILE` 和 `OPENALEX_EMAIL`。不要把真实密钥写入本目录之外的示例、JSON、日志或提交内容。
