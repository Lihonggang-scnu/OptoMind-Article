# T019 五条检索式快速诊断

固定输入：提交 `ba2bb8ff2183b478fb8bc8591fcedc8f07bbebd4` 的 `handoff/R3-T019-D20/rejected-drafts/round-1.task_sheet.json`。5条search_queries原样使用，仅各自原paper_search/snippet_search端点，每条limit=10、一次尝试。使用现有S2配置与检索程序，无Qwen/付费模型、题录补齐、全文下载、测试、仿真或PDF。被拒草案仍被拒，本诊断不改变原文回显校验或生产代码。

| 查询 | 端点 | 返回 | 快速判断与值得读的条目 |
|---|---|---:|---|
| Q1 | paper_search | 10篇，6篇有摘要 | 有相近器件及方法线索。优先第1、5条；第6条只作不同结构的方法参考。第2、3条只有题名，尚不能判断设计价值。混有传感应用、圆偏振分束和中红外背景。 |
| Q2 | snippet_search | 0，HTTP 429 | 限流失败，不能判断检索式质量，也不等于没有相关文献。 |
| Q3 | paper_search | 0，HTTP 429 | 限流失败，不能评价TiO2/SiO2材料检索效果。 |
| Q4 | snippet_search | 0，HTTP 429 | 限流失败，有限层数优化检索尚无结果证据。 |
| Q5 | snippet_search | 10片段、7篇来源 | 9个body、1个title。优先第1/6、8、4/9条；第2/3条也值得合并读，第5条是方法候选。 |

合计20条返回、17篇不同来源。Q5中3篇各有两个不同片段，属于同文复现，不是6篇独立论文；Q1与Q5按返回CorpusId未见重合。3次429均保存原始错误，继续其余查询，没有追加重试或扩大检索。

## 最值得读的5条来源

1. **[Dichroic beam splitter for visible, short-wave infrared, and mid-wave infrared](https://doi.org/10.1117/1.OE.61.10.105111)**（Q1-1，2022，CorpusId 253178329）。摘要明确为多光谱载荷设计和仿真45°二向色分光器，最接近器件路线。材料、层数与膜厚未在返回摘要中出现，SWIR/MWIR波段也不等于本题波段；值得后续阅读设计章节。
2. **[Preparation of High-Performance Metal-Free UV/Near Infrared-Shielding Films for Human Skin Protection](https://www.semanticscholar.org/paper/CorpusID:237322570)**（Q5-1/6，正文）。直接给出TiO2/SiO2配对1–3、约950nm Bragg波长，以及SiO2约163.8nm、TiO2约105.6nm厚度，具有具体膜层参数线索。但原用途为NIR屏蔽，其谱功能与本题NIR透过不同，不能原样采用。
3. **[Systems Approach to Polarization Calibration for the Daniel K. Inouye Solar Telescope (DKIST)](https://www.semanticscholar.org/paper/CorpusID:256154206)**（Q5-8，正文）。FIDO二向色膜、380–1000/1050–1800nm谱段、熔融石英基底、窄光谱特征及膜厚敏感性均有现成线索，可帮助理解相近器件的光谱/偏振与容差验收；未返回本题精确层配方。
4. **[Design and modeling of an oblique incidence dichroic mirror for a high contrast imaging system](https://www.semanticscholar.org/paper/CorpusID:290830736)**（Q5-4/9，正文）。明确解释斜入射s/p响应、二向衰减、迟滞与色散波前误差，适合作为机制阅读。当前片段没有证明材料组合、膜厚或<=24层优化。
5. **[Spectral Splitting Optimization for Highly Efficient Hybrid Photovoltaic Devices by Using Na3AlF6, Y2O3 and TiO2 Beam Splitter](https://doi.org/10.4028/p-bis73b)**（Q1-5，2022，摘要）。包含45°、400–1000nm、56层[LMHM]14与Optimac精修等实际设计/优化线索。材料含Na3AlF6/Y2O3，且是可见透过、红外反射，方向与本题相反；价值在方法迁移，不能作为<=24层TiO2/SiO2方案通过的证据。

## 效果与缺口

- **Q1偏宽**：能找器件，但前10混入酒精测量系统、圆偏振分束和中红外镀膜。宜按已有摘要优先筛读，不把所有结果当作设计证据。
- **Q5可用但有同文多片段**：TiO2/SiO2参数和斜入射机制分别有所收获；第10条是title型片段，不能冒充正文。DKIST两篇不同论文仍是相近仪器背景，可合并排读。
- **有限层数优化仍缺直接证据**：Q4限流；Q5-5的层级粒子群题名值得留意，但返回正文只有角度稳定性，不能确认算法、层数限制或优化收益。Q1-5是56层，超出本题<=24层。
- 三条限流查询的效果和跨查询重复无法评价。本轮未检到同时满足全部目标、材料、基底、45°和<=24层的已确认方案；这不影响上述部分线索的阅读价值。

原始HTTP返回在 `raw/`，每条的状态、标题、可用摘要/片段、snippetKind、section、来源ID/链接和原始行在 `queries/Q1.json` 至 `Q5.json`。逐条阅读判断见 `RESULT_REVIEW.json`。缺失题录保持缺失，未另行查询。没有新增费用或账本变更。本次诊断完成即停。
