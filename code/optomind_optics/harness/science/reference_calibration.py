"""Linear reference-offset calibration with explicit shared-error covariance.

A corrected spectrum is a derived view of two purchases, never a new acquisition.
The physical assumption is an additive deterministic instrument offset shared by
reference and specimen; independent/shared stochastic terms follow receipts.
"""
import numpy as np


def correct_reference(sample, reference, *, expected_reference):
    def unpack(receipt):
        raw=receipt.get("provider_receipt",{}).get("measurement_receipt",{})
        required={"wavelengths_nm","values","covariance","calibration_group","calibration_std"}
        if not required <= set(raw): raise ValueError("reference calibration requires full raw error receipts")
        values=np.asarray(raw["values"],float);cov=np.asarray(raw["covariance"],float)
        if cov.shape!=(len(values),len(values)) or not np.isfinite(values).all() or not np.isfinite(cov).all():
            raise ValueError("invalid calibration data")
        return raw,values,cov
    s,x,cs=unpack(sample);r,y,cr=unpack(reference)
    if tuple(s["wavelengths_nm"])!=tuple(r["wavelengths_nm"]):
        raise ValueError("reference coordinates must align; implicit interpolation forbidden")
    target=np.broadcast_to(np.asarray(expected_reference,float),x.shape)
    if not np.isfinite(target).all(): raise ValueError("finite frozen reference expectation required")
    ss=float(s["calibration_std"]);rs=float(r["calibration_std"])
    same=s["calibration_group"]==r["calibration_group"]
    covariance=cs+cr+np.ones_like(cs)*(ss*ss+rs*rs-(2*ss*rs if same else 0.))
    if sample.get("acquisition_id")==reference.get("acquisition_id"):
        raise ValueError("specimen and control must be distinct acquisitions")
    corrected=x-y+target
    return {"values":corrected.tolist(),"coordinate":list(s["wavelengths_nm"]),"covariance":covariance.tolist(),
            "derived_from":[sample.get("acquisition_id"),reference.get("acquisition_id")],
            "shared_reference_id":reference.get("acquisition_id"),"new_evidence":False,
            "method":"linear_additive_reference_offset", "assumption":"shared deterministic additive offset",
            "uncertainty_scope":"independent raw covariance plus receipt-declared shared calibration; reference expectation treated fixed",
            "cross_view_covariance_required":True}
