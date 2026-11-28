from collections.abc import Sequence

from haiway import Observability, ObservabilityLink
from haiway.context import ContextIdentifier


def linking(
    base: Observability,
    links: Sequence[ObservabilityLink],
) -> Observability:
    def scope_entering(
        scope: ContextIdentifier,
        /,
        *,
        links: Sequence[ObservabilityLink],
    ) -> str:
        return base.scope_entering(scope, links=injected)

    injected = links
    return Observability(
        trace_identifying=base.trace_identifying,
        log_recording=base.log_recording,
        event_recording=base.event_recording,
        metric_recording=base.metric_recording,
        attributes_recording=base.attributes_recording,
        scope_entering=scope_entering,
        scope_exiting=base.scope_exiting,
        trace_context_encoding=base.trace_context_encoding,
        level_checking=base.level_checking,
    )
