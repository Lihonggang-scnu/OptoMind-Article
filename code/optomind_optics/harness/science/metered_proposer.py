"""Explicit bridge to Review's existing Qwen client and persistent dual ledger.

The host supplies an already configured client and authorizes QwenJointProposer
separately. This module never constructs ledgers, resolves credentials, changes
caps, retries, or converts a failed paid call into a mock result. Review must be
installed/importable only when this optional adapter is constructed.
"""
from pathlib import Path
import math


class QwenDirectMeteredAdapter:
    """Use Review's pre-dispatch reservation and usage settlement unchanged.

    Require one explicitly selected credential and one attempt. Unknown outcomes
    retain Review's conservative holds; the host must reconcile, never retry
    implicitly. The configured prompt counter (or Review's conservative UTF-8
    bound) and reasoning budget remain part of its exact request envelope.
    """

    def __init__(self, *, client):
        self.client = client
        self._validate()

    def _validate(self):
        from optomind_research.runtime.upgrade3.module4.runtime import (
            GlobalBudgetLedger, QwenDirectClient,
        )
        from optomind_research.runtime.upgrade3.dual_budget import DualBudgetLedger

        client = self.client
        if not isinstance(client, QwenDirectClient):
            raise TypeError("existing QwenDirectClient required")
        if client.max_retries != 0 or client.max_keys != 1:
            raise ValueError("metered proposals require one attempt and one key")
        if client.key_file is None or client.key_index is None:
            raise ValueError("explicit key_file and key_index required; no ambient credentials")
        ledger = client.budget_ledger
        if not isinstance(ledger, DualBudgetLedger):
            raise TypeError("existing DualBudgetLedger required")
        paths = []
        for scope in (ledger.project, ledger.total):
            if not isinstance(scope, GlobalBudgetLedger) or not scope.path:
                raise ValueError("distinct persistent capped ledgers required")
            path = Path(scope.path).resolve()
            if not path.is_file():
                raise ValueError("existing persistent ledger missing; no budget reset")
            state = scope.as_dict()
            cap = state["limit_cny"]
            if cap is None or isinstance(cap, bool) or not math.isfinite(float(cap)) or float(cap) <= 0:
                raise ValueError("finite positive project and account caps required")
            paths.append(path)
        if paths[0] == paths[1] or paths[0].samefile(paths[1]):
            raise ValueError("distinct persistent project and account ledgers required")

    def call_metered(self, messages, *, max_tokens, force_mock=False):
        if force_mock is not False:
            raise ValueError("metered adapter has no mock fallback; use ReplayJointProposer")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 64 <= max_tokens <= 16000:
            raise ValueError("max_tokens must be an integer from 64 to 16000")
        self._validate()  # Recheck mutable client configuration before dispatch.
        if max_tokens > self.client.max_output_tokens:
            raise ValueError("proposal token cap exceeds configured client cap")
        result = self.client(messages, max_output_tokens=max_tokens)
        # Review settles before returning, including for invalid proposal JSON.
        # Preserve its complete result and unmodified provider counters. Missing
        # usage is not synthesized: the underlying ledger retains an unknown hold.
        return {**result, "_llm_usage": dict(result["usage"])}
