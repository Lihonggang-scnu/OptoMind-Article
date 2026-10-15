"""Local review of the five completed queries; no network/model calls."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
q1=read(ROOT/'queries/Q1.json')['results']
q5=read(ROOT/'queries/Q5.json')['results']
notes={
 'Q1':[
  ('priority','45°可见—SWIR/MWIR二向色分光器件设计/仿真，器件最相近；本条摘要未给材料、膜厚和层数，波段也非本题原区间。'),
  ('title_only_lead','题名直接涉及多层二向色分光器设计/制造，但未返回摘要，尚不能判断参数或适用性。'),
  ('title_only_lead','同类仪器分光器题名线索；缺摘要，不能推定材料、角度和膜系。'),
  ('background_or_offtopic','酒精浓度测量和散射补偿应用，缺膜系设计/优化信息，优先级低。'),
  ('priority_method_transfer','45°、400–1000nm、56层[LMHM]14和Optimac精修均有摘要证据；材料Na3AlF6/Y2O3/TiO2、可见透过/NIR反射与本题不同，只作方法迁移。'),
  ('secondary_method_lead','给出TiO2/空气隙的7层光学厚度序列和仿真实验，值得了解少层设计表达；属于偏振分束，空气隙/硼硅玻璃不符合本题材料/基底约束。'),
  ('background_or_offtopic','圆偏振分束与受抑全反射路线；无摘要，不能据题名当作目标分光膜方案。'),
  ('application_background','可见与InGaAs近红外双通道成像、955nm分光边缘，应用相近；摘要没有膜层设计参数。'),
  ('title_only_lead','同时可见/NIR成像相机的题名线索，未给摘要，未证实膜系细节。'),
  ('background_or_offtopic','主要涉及3–15微米、Ge/YbF3等材料和67.5°角；对当前可见/NIR TiO2/SiO2目标较远。'),
 ],
 'Q5':[
  ('priority_parameters','正文给出TiO2/SiO2配对1–3、约950nm Bragg目标、SiO2约163.8nm和TiO2约105.6nm厚度；用途为NIR屏蔽，不能直接移植成NIR透过方案。'),
  ('useful_mechanism','DKIST二向色膜反射/透过要求、0–45°测试、迟滞/二向衰减和TFCalc对照；有实测机制线索，波段不同。'),
  ('useful_mechanism_duplicate_paper','同一DKIST论文的另一正文片段：未指定偏振优化时的强光谱偏振变化；与第2条合并阅读。'),
  ('priority_mechanism','斜入射二向色镜设计/建模正文说明s/p边界条件、振幅相位差及波前误差；是机制线索，当前片段未给膜厚/层数。'),
  ('secondary_method_lead','层级粒子群优化的题名加TiO2/SiO2约45°稳定性正文，可作方法候选；当前片段不含优化细节或有限层数证据。'),
  ('useful_parameters_duplicate_paper','与第1条同文，正文补充约760–1200nm屏蔽带、约420nm带宽与结构/TEM材料证据；合并读，避免重复当作独立来源。'),
  ('general_mechanism_background','投影/全息应用的斜入射s/p谱移、非平行光影响；解释边缘变差有用，但非目标膜系参数来源。'),
  ('priority_analogous_device','DKIST FIDO二向色分光的380–1000/1050–1800nm谱段、熔融石英基底、窄谱特征与厚度敏感性均有正文证据；可作相近器件和验收风险参考。'),
  ('useful_mechanism_duplicate_paper','与第4条同文，补充s/p的R/T、二向衰减/迟滞定义与角度效应；合并读。'),
  ('title_only_background','snippetKind=title，只有题名；Ti/TiO2/SiO2还包含金属Ti，不能当作两种介质体系或正文证据。'),
 ]}
rows=[]
for query,results in [('Q1',q1),('Q5',q5)]:
    for row,(category,reason) in zip(results,notes[query],strict=True):
        rows.append({'query_id':query,'rank':row['rank'],'title':row['title'],
            'corpus_id':row['corpus_id'],'source_link':row['source_link'],
            'content_kind':row['content_kind'],'snippet_kind':row['snippet_kind'],
            'review_category':category,'reason':reason})
with (ROOT/'RESULT_REVIEW.json').open('x',encoding='utf-8',newline='\n') as f:
    json.dump(rows,f,ensure_ascii=False,indent=2);f.write('\n')
report='''# T019 五条检索式快速诊断

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
'''
with (ROOT/'REPORT.md').open('x',encoding='utf-8',newline='\n') as f:f.write(report)
print('Short report and20 returned-item notes saved; no more API calls.')
