"""Task repo, instantiation, and assignment (design doc 02)."""

from agentlab.tasks.manifest import generate_manifest
from agentlab.tasks.models import Manifest, TaskInstance, TaskMeta
from agentlab.tasks.params import sample_params
from agentlab.tasks.repo import TaskRepo

__all__ = [
    "Manifest",
    "TaskInstance",
    "TaskMeta",
    "TaskRepo",
    "generate_manifest",
    "sample_params",
]
