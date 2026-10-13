"""Independent three-layer purchased observations for the C13 offline adapter.

Reuses C17's independently verified Byrnes implementation. Hidden constitutive
parameters stay in this evaluator; the policy only gets WorldClient receipts.
In-process fixtures do not establish an OS security boundary or real-device data.
"""
from dataclasses import asdict
import hashlib
import json
import numpy as np
from .multilayer_tasks import byrnes_prediction
from optomind_optics.harness.science.world_client import MeasurementReceipt


class MultilayerMeasurementService:
    def __init__(self, *, sessions, reference_session, seed, family, budget,
                 noise_std=.002, calibration_std=.001, cost=1.):
        if family not in {"F0","F3","F4","F5"}:raise ValueError("unsupported multilayer fixture family")
        self.__sessions=dict(sessions);self.__reference=reference_session;self.__seed=seed
        self.__remaining=float(budget);self.__noise=noise_std;self.__calibration=calibration_std;self.__cost=cost
        self.__purchases={}
        rng=np.random.default_rng(seed)
        self.__truth={"high_n":float(rng.uniform(1.8,2.3)),"thickness_scale":1.}
        self.__design=(65.,float(rng.uniform(70.,140.)),95.)
        self.__dispersion=.035 if family in {"F3","F4"} else 0.
        self.__gain=.94 if family in {"F4","F5"} else 1.
        self.__offset=.015 if family in {"F4","F5"} else 0.

    def handle(self, request):
        try:
            if request["operation"]!="purchase":raise ValueError("only purchased raw observations supported")
            task=request["task_id"];channel=request["channel"];key=(task,request["request_id"])
            if task not in self.__sessions or channel not in {"R","T"} or not key[1]:raise ValueError("invalid task/channel/request")
            binding=json.dumps(request,sort_keys=True,allow_nan=False)
            if key in self.__purchases:
                old,response=self.__purchases[key]
                if old!=binding:raise ValueError("idempotency key changed")
                return response
            grid=np.asarray(request["wavelengths_nm"],float)
            if grid.ndim!=1 or not 2<=len(grid)<=4096 or not np.isfinite(grid).all() or np.any(np.diff(grid)<=0) or grid.min()<400 or grid.max()>900:
                raise ValueError("outside frozen normal-incidence 400–900 nm domain")
            if self.__remaining<self.__cost:raise ValueError("measurement budget exhausted")
            if task==self.__reference:
                # Known bare n=1.5 reference coupon, distinct from n=1.52 substrate.
                values=np.full(len(grid),.04 if channel=="R" else .96)
            else:
                values=np.array([byrnes_prediction({**self.__truth,"high_n":self.__truth["high_n"]+self.__dispersion/(wl/1000.)**2},
                    self.__design,[wl],channel)[0] for wl in grid])
                values=self.__gain*values+self.__offset
            digest=lambda value:hashlib.sha256(value.encode()).digest()
            group=self.__sessions[task]
            rng=np.random.default_rng(int.from_bytes(digest(f"{self.__seed}:{binding}")[:8],"big"))
            shared=np.random.default_rng(int.from_bytes(digest(f"{self.__seed}:{group}")[:8],"big")).normal()
            values+=self.__noise*rng.normal(size=len(grid))+self.__calibration*shared
            rid=hashlib.sha256(binding.encode()).hexdigest()
            response=asdict(MeasurementReceipt(rid,task,key[1],channel,tuple(map(float,grid)),tuple(map(float,values)),
                tuple(tuple(map(float,row)) for row in np.eye(len(grid))*self.__noise**2),group,self.__calibration,
                self.__cost,"multilayer-byrnes/1",rid))
            self.__remaining-=self.__cost;self.__purchases[key]=(binding,response)
            return response
        except (KeyError,ValueError) as exc:return {"error":str(exc)}
