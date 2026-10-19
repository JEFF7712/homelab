from __future__ import annotations

from datetime import datetime

from .authorization import Policy
from .codec import document
from .errors import Code, ProtocolError
from .models import Discovery, Plan, Project, operations, timestamp


def plan(project: Project, discovery: Discovery, policy: Policy, now: datetime) -> Plan:
    project = Project.parse(document(project))
    discovery = Discovery.parse(document(discovery))
    candidate = Plan(
        1,
        project,
        policy.principal,
        policy.platform,
        policy.policy_digest,
        discovery,
        ("pending", "enrolled"),
        operations(),
    ).validated()
    policy.check(candidate, candidate.principal)
    if discovery.repository_state == "unknown":
        raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
    if now.tzinfo is None:
        raise ProtocolError(Code.INVALID_SPEC)
    age = (now - timestamp(discovery.observed_at)).total_seconds()
    if not 0 <= age <= policy.max_discovery_age_seconds:
        raise ProtocolError(Code.PLAN_STALE)
    if discovery.source_sha is not None and discovery.source_sha != project.source_sha:
        raise ProtocolError(Code.SOURCE_MOVED)
    return candidate
