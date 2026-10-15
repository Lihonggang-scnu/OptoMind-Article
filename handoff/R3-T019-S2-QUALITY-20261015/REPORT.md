# T019 五条检索式：结果质量与阅读优先级

原样使用提交 `ba2bb8ff2183b478fb8bc8591fcedc8f07bbebd4` 中被拒草案的5条search_queries及各自端点。以下仅评价最终成功返回的结果；每条10条，共50条、42个不同CorpusID。ID计数未消除同文异ID，不等于42篇已核实独立论文。

| 查询 | 返回内容 | 质量判断、值得读的条目 |
|---|---|---|
| Q1：相近二向色分光器关键词 | 10篇；6摘要、4仅元数据 | **中等，有器件线索**。第1条45°分光器最有用，第5条可作优化方法参照，第6条是不同材料/功能的少层参考；混入应用系统、圆偏振和中红外背景。 |
| Q2：相近多光谱分光器自然问句 | 10片段；7正文、3摘要 | **中等偏低**。第5条SIDE可见反射/NIR透过分光器很有价值，第3条多通道分光也可读，第9条提供膜层/偏振约束线索。混入UV金属膜、颜料与建筑材料成像。 |
| Q3：TiO2 SiO2 dichroic coating oblique incidence | 10篇；7摘要、3仅元数据 | **偏低，歧义和噪声明显**。优先第3条二向色镜设计、第5条TiO2/SiO2角度谱移，第10条仅作题名线索。斜角沉积被误当光学斜入射，还出现焊接条目。 |
| Q4：有限层数edge-filter优化 | 10片段；9正文、1摘要，8篇来源ID | **方法线索可用，直接案例不足**。第9条厚度梯度/TMM优化、第5/7条非等厚五层IR结构值得读；明显混入空间图像edge detection、DWDM和微波滤波。 |
| Q5：TiO2/SiO2斜入射偏振/边缘机制 | 10片段；9正文、1题名，7篇来源ID | **五条中较好**。第1/6条有材料与厚度参数，第2/3、4/9条解释斜入射s/p，第8条提供近似分光器和基底线索。第10条仅题名，不能当正文证据。 |

## 最值得读的5条来源

1. **[The SIDE dual VIS-NIR fiber fed spectrograph for the 10.4 m Gran Telescopio Canarias](https://www.semanticscholar.org/paper/CorpusID:119291481)**（Q2-5，正文）。可见反射、NIR透过、熔融石英基底，与本题器件功能最接近；有约950nm分界、约50nm过渡区和R/T规格线索。但分界不同于本题650–700nm过渡，片段未给层配方，不能直接当作满足原题的方案。
2. **[Dichroic beam splitter for visible, short-wave infrared, and mid-wave infrared](https://doi.org/10.1117/1.OE.61.10.105111)**（Q1-1，摘要）。明确多光谱分光器的45°设计与仿真，适合阅读器件设计路线；SWIR/MWIR区间与本题不同，摘要未提供材料、层数和膜厚。
3. **[Preparation of High-Performance Metal-Free UV/Near Infrared-Shielding Films for Human Skin Protection](https://www.semanticscholar.org/paper/CorpusID:237322570)**（Q5-1/6，正文）。直接给TiO2/SiO2配对1–3、约950nm Bragg目标，以及SiO2约163.8nm、TiO2约105.6nm厚度。可用于材料/膜层参数阅读；原用途是NIR屏蔽，不能原样移植为NIR透过方案。
4. **[Design and modeling of an oblique incidence dichroic mirror for a high contrast imaging system](https://doi.org/10.1117/12.3105700)**（Q3-3摘要＋Q5-4/9正文）。包含短波反射/长波透过、反射相位与膜厚误差优化、s/p振幅相位差。实际摘要条件是7°和约550nm切换，非本题45°；有用的是设计与偏振/容差方法。
5. **[High-NA optical edge detection via optimized multilayer films](https://www.semanticscholar.org/paper/CorpusID:231418907)**（Q4-8/9，正文）。固定材料，以逐层厚度作为自由度，对角度响应误差进行优化，并描述类似needle方法的梯度与矩阵计算。算法迁移价值较高；目标是图像空间边缘检测，不是可见/NIR光谱分光，未证明本题<=24层可达。

备用：Q4-5/7的 **[Selective unequal-thickness thin-film filters for IR spectral region](https://www.semanticscholar.org/paper/CorpusID:115041881)** 明确五层结构，是少层设计线索，但材料为PbTe/GeTe/SnTe且目标是IR窄带。Q2-9/Q5-2/3的DKIST偏振论文给出15°、96层/8.8微米等条件，可帮助认识层数和偏振代价，不能当作本题24层以内的证据。

## 重复、误召回与使用限制

- Q2-7与Q5-8为同一DKIST正文片段；Q2-9与Q5-2/3是同文不同段落。Q3-3与Q5-4/9也是同文，合并阅读。
- Q4-5/7、Q4-8/9分别同文；Q5共有三组同文双片段。Q2-1/2的题名和摘要完全一致但CorpusID不同，属疑似重复题录，未补齐或擅自合并身份。
- Q2的多光谱应用词太宽；Q3的oblique incidence混入斜角沉积；Q4的edge混入图像边缘检测。三者均不宜把前10全部交给下游当设计证据。
- 现有结果没有确认一份同时满足原波段、45°、非偏振、熔融石英、仅TiO2/SiO2且<=24层的可复用方案。各线索仍可分别帮助器件路线、材料参数和算法选择。片段内部分公式缺失，不能据此直接实现算法。
- 所有判断只依据本次返回的标题、可用摘要/片段。未补齐题录、未下载全文、未追加模型分析。被拒任务单状态与生产校验保持原样。

原始成功返回：`raw/Q1.response.json` 至 `Q5.response.json`。每条标题、摘要/片段、snippetKind/section、原始行、来源ID/链接在 `queries/`；逐条阅读判断见 `RESULT_REVIEW.json`。检索已停止。
