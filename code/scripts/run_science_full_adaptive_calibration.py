"""Run a frozen complete-workflow null/alternative audit, offline only."""
import argparse,json
from pathlib import Path
from optomind_optics.harness.science.adaptive_calibration import full_adaptive_audit

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    args=p.parse_args();payload=json.loads(args.config.read_text());full_adaptive_audit(args.output,**payload)
if __name__=='__main__':main()
