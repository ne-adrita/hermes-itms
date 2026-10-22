"""
The mandatory spend / attempt gate for a live benchmark run.

Why this exists
---------------
A live run issues one provider request per attempt.  Without a hard limit a
mistyped ``--tasks``/``--reps`` pair, or a model id that is accidentally a paid
route, can spend real money with no way to stop it.  The gate therefore
enforces **two independent limits before any request is made**:

1.  an **attempt cap** - the number of provider requests the run may issue.
    This is the binding guard whenever the provider does not report a cost,
    which is the case for the chat-completions endpoint this project uses
    (0 of 120 attempts in the historical live run reported a cost).
2.  a **reported-spend cap** - an upper bound on the cumulative cost the
    provider *reported*.  It is never applied to an estimate, because an
    estimate is not a spend.

The gate fails closed: with no explicit attempt cap a live run cannot start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["BudgetError", "BudgetGate"]


class BudgetError(RuntimeError):
    """Raised when a configured budget limit forbids the next request."""


@dataclass
class BudgetGate:
    """
    Enforces the request budget of a run.

    Parameters
    ----------
    max_attempts:
        The hard cap on provider requests.  Mandatory (must be > 0).
    max_spend_usd:
        Optional cap on the cumulative **reported** cost.  When the provider
        does not report a cost, this cap cannot fire; the gate records that
        fact instead of pretending to enforce it.
    require_free_model:
        When true, the run refuses to start on a model id that is not a
        ``:free`` route.
    model_id:
        The pinned model id, used by the free-model guard.
    """

    max_attempts: int
    max_spend_usd: Optional[float] = None
    require_free_model: bool = False
    model_id: Optional[str] = None

    #: Number of requests that were actually issued.
    requests_issued: int = 0
    #: Cumulative cost the provider *reported* (never an estimate).
    cumulative_reported_spend_usd: float = 0.0
    #: Attempts whose cost was not reported by the provider.
    unreported_cost_attempts: int = 0
    #: The reason the gate stopped the run, if it did.
    limit_reached_reason: Optional[str] = None
    #: The cells that were planned but never requested.
    not_executed: List[str] = field(default_factory=list)

    # -- preflight -------------------------------------------------------
    def preflight(self) -> Dict[str, Any]:
        """
        Validates the budget configuration.  Raises :class:`BudgetError` when
        the configuration would make the run unbounded or unsafe.
        """
        if int(self.max_attempts) < 1:
            raise BudgetError(
                "max_attempts must be a positive explicit limit: a run without "
                "a request cap is unbounded. Pass --max-attempts explicitly."
            )
        if self.max_spend_usd is not None and float(self.max_spend_usd) <= 0:
            raise BudgetError(
                f"max_spend_usd must be a positive explicit limit; got "
                f"{self.max_spend_usd!r}"
            )
        if self.require_free_model:
            model = (self.model_id or "").strip()
            if not model:
                raise BudgetError(
                    "require_free_model is set but no model id was supplied."
                )
            if not model.endswith(":free"):
                raise BudgetError(
                    f"model id '{model}' is not a ':free' route; refusing to start "
                    f"an unbounded-spend run while the free-model guard is in force."
                )
        return self.state()

    # -- per-attempt -----------------------------------------------------
    def before_request(self, cell_key: Optional[str] = None) -> None:
        """
        Called **before** every provider request.  Raises :class:`BudgetError`
        when the next request would exceed a limit.
        """
        if self.requests_issued >= int(self.max_attempts):
            self.limit_reached_reason = (
                f"attempt cap reached: {self.requests_issued} of "
                f"{int(self.max_attempts)} requests issued"
            )
            if cell_key:
                self.not_executed.append(cell_key)
            raise BudgetError(self.limit_reached_reason)

    def after_request(self, reported_cost_usd: Optional[float]) -> None:
        """
        Called after every provider request.  Charges the **reported** cost
        against the spend cap and records attempts whose cost was not reported.
        """
        self.requests_issued += 1
        if reported_cost_usd is None:
            self.unreported_cost_attempts += 1
            return
        self.cumulative_reported_spend_usd += float(reported_cost_usd)
        if (
            self.max_spend_usd is not None
            and self.cumulative_reported_spend_usd >= float(self.max_spend_usd)
        ):
            self.limit_reached_reason = (
                f"reported spend ${self.cumulative_reported_spend_usd:.6f} reached "
                f"the configured maximum of ${float(self.max_spend_usd):.6f}"
            )

    def spend_cap_reached(self) -> bool:
        if self.max_spend_usd is None:
            return False
        return self.cumulative_reported_spend_usd >= float(self.max_spend_usd)

    # -- reporting -------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        """Returns the budget state.  It contains no credential and no price data."""
        return {
            "max_attempts": int(self.max_attempts),
            "max_spend_usd": self.max_spend_usd,
            "require_free_model": bool(self.require_free_model),
            "model_id": self.model_id,
            "requests_issued": self.requests_issued,
            "cumulative_reported_spend_usd": round(self.cumulative_reported_spend_usd, 8),
            "unreported_cost_attempts": self.unreported_cost_attempts,
            "cost_reporting_available": self.unreported_cost_attempts == 0
            and self.requests_issued > 0,
            "spend_cap_effective": (
                bool(self.max_spend_usd is not None)
                and self.unreported_cost_attempts == 0
            ),
            "limit_reached_reason": self.limit_reached_reason,
            "not_executed_cells": list(self.not_executed),
        }
