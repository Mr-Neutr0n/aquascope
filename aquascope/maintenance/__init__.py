"""Maintenance loops that keep the collectors alive: diagnose, propose, verify, hand to a human."""

from aquascope.maintenance.repair import (
    Evidence,
    Proposal,
    Verification,
    apply_and_verify,
    gather_evidence,
    propose_repair,
    repair_source,
)

__all__ = ["Evidence", "Proposal", "Verification", "apply_and_verify", "gather_evidence", "propose_repair",
           "repair_source"]
