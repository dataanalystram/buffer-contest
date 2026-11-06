"""This process's id (dependency-free, so the video worker can import takes.py): a run or in-process job stamped with
another process's id whose heartbeat stopped was cut off by a deploy or a crash (agent_brain, take_control)."""
import uuid

BOOT = uuid.uuid4().hex[:12]
