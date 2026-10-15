from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

out=Path(r'D:\OptoMind-Article-3\r3t030a\handoff\R3-T030-DIRECTIONAL-20261016\science')
chains=json.loads((out/'REPRESENTATIVE_CHAINS.json').read_text(encoding='utf-8'))
designs=json.loads((out/'REPRESENTATIVE_CERTIFIED_DESIGNS.json').read_text(encoding='utf-8'))['designs']
chosen=[]
for goal in ['G1','G2']:
    c=max(chains,key=lambda x:x['child']['deltas_from_parent'][goal])
    if c not in chosen:chosen.append(c)
fig,axes=plt.subplots(len(chosen),2,figsize=(10,3.5*len(chosen)),squeeze=False)
for row,c in enumerate(chosen):
    parent=designs[c['parent_design_id']];child=designs[c['child']['design_id']]
    for col,metric in enumerate(['R','T']):
        ax=axes[row,col]
        for d,label,style in [(parent,'Original parent','--'),(child,'T030 certified child','-')]:
            raw=d['certified_result']['raw_result'];channel=raw['channels']['angle=45|pol=unpolarized']
            ax.plot(raw['wavelengths_nm'],channel[metric],style,label=label,linewidth=1.3)
        ax.set_title(c['branch_id']+' / '+metric,fontsize=9);ax.set_xlabel('Wavelength (nm)');ax.set_ylabel('Power '+metric);ax.set_ylim(-.02,1.02);ax.grid(alpha=.2);ax.legend(fontsize=8)
fig.suptitle('Actual certified parent/child spectra, 45 deg unpolarized',fontsize=11)
fig.tight_layout();fig.savefig(out/'parent_child_spectra.png',dpi=170);plt.close(fig)
(out/'PLOTTED_CHAIN_IDENTITIES.json').write_text(json.dumps(chosen,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
