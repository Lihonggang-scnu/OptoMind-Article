# 凭证配置

本目录只提供空的凭证文件模板。每个文本文件使用“一行一个密钥”的格式；凭证由运行者在本地安全填写，不进入源代码、运行日志或公开版本库。

当前 harness 使用：

- `qwen-api-key.txt`：Qwen 服务密钥；也可通过 `QWEN_API_KEY_FILE` 或 `DASHSCOPE_API_KEY_FILE` 指向其他安全位置。
- `semantic-scholar-api-key.txt`：Semantic Scholar 方法检索密钥；也可通过 `SEMANTIC_SCHOLAR_API_KEYS_FILE` 指向其他安全位置。

若启用 OpenAlex 检索，可在本地另行配置 `OPENALEX_API_KEYS_FILE` 和 `OPENALEX_EMAIL`。不要把真实密钥写入本目录之外的示例、JSON、日志或提交内容。

## 评审使用

项目方私下提供本文件夹时，评委无需打开或复制密钥内容，只需用收到的整个 `api_keys` 文件夹替换仓库中的 `code/api_keys`，然后在仓库根目录双击 `RUN_LIGHT_TEST.cmd`。跨平台用户执行 `python3 quickstart.py test`。快捷入口会自动检查文件是否非空、准备依赖、运行有界真实测试并打开回放台。

静态回放不需要本文件夹中的任何真实凭证；双击根目录的 `START_REPLAY.cmd` 即可查看六组正式记录。
