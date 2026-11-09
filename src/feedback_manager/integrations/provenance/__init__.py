"""Provenance providers: link feedback to the execution records of a run.

`AgxaiProvenanceProvider` reads ``agxai`` records; `create_agxai_runtime`
builds an ``agxai`` runtime with any of its components replaced. Implement
`feedback_manager.contracts.ProvenanceProvider` for other systems.
"""

from feedback_manager.integrations.provenance.agxai import (
    AgxaiProvenanceProvider,
    create_agxai_runtime,
)

__all__ = ["AgxaiProvenanceProvider", "create_agxai_runtime"]
