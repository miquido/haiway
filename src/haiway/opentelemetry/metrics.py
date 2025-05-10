# from time import monotonic
# from typing import Any, Self, TypeVar, final

# from opentelemetry import metrics, trace
# from opentelemetry.context import Context
# from opentelemetry.metrics import Meter
# from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
# from opentelemetry.sdk.metrics.export import (
#     ConsoleMetricExporter,
#     PeriodicExportingMetricReader,
# )
# from opentelemetry.sdk.resources import Resource
# from opentelemetry.sdk.trace import TracerProvider
# from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
# from opentelemetry.trace import Span, Tracer

# from haiway.context import MetricsHandler, ScopeIdentifier
# from haiway.state import State

# T = TypeVar("T")

# __all__ = ("OpenTelemetryMetrics",)


# class OpenTelemetryScope:
#     __slots__ = (
#         "exited",
#         "identifier",
#         "nested",
#         "span",
#     )

#     def __init__(
#         self,
#         identifier: ScopeIdentifier,
#         /,
#         span: Span,
#     ) -> None:
#         self.identifier: ScopeIdentifier = identifier
#         self.exited: bool = False
#         self.span: Span = span
#         self.nested: list[OpenTelemetryScope] = []

#     @property
#     def finished(self) -> float:
#         return self.exited is not None and all(nested.finished for nested in self.nested)

#     def record(
#         self,
#         metric: str,
#         /,
#         attributes: dict[str, Any],
#     ) -> None: ...

#     def exit(self) -> None:
#         pass

#     def finalize(self) -> None:
#         pass


# @final
# class OpenTelemetryMetrics:
#     @classmethod
#     def configure(
#         cls,
#         *,
#         service: str,
#         export_interval_millis: int = 10000,
#     ) -> type[Self]:
#         # Create shared resource for both metrics and traces
#         resource: Resource = Resource.create({"service.name": service})

#         # Set up metrics provider
#         meter_provider: SdkMeterProvider = SdkMeterProvider(
#             resource=resource,
#             metric_readers=[
#                 PeriodicExportingMetricReader(
#                     ConsoleMetricExporter(),
#                     export_interval_millis=export_interval_millis,
#                 )
#             ],
#         )
#         metrics.set_meter_provider(meter_provider)

#         # Set up trace provider
#         tracer_provider: TracerProvider = TracerProvider(resource=resource)
#         span_processor: BatchSpanProcessor = BatchSpanProcessor(ConsoleSpanExporter())
#         tracer_provider.add_span_processor(span_processor)
#         trace.set_tracer_provider(tracer_provider)
#         return cls

#     @classmethod
#     def handler(cls) -> MetricsHandler:
#         """Create a metrics handler using OpenTelemetry for metrics collection.

#         Returns:
#             A MetricsHandler configured to use OpenTelemetry
#         """

#         handler: Self = cls()
#         return MetricsHandler(
#             record=handler.record,
#             enter_scope=handler.enter_scope,
#             exit_scope=handler.exit_scope,
#         )

#     __slots__ = (
#         "_meter",
#         "_root_scope",
#         "_scopes",
#         "_tracer",
#     )

#     def __init__(self) -> None:
#         """Initialize the OpenTelemetry metrics collector."""
#         self._meter: Meter | None = None
#         self._tracer: Tracer | None = None
#         self._root_scope: OpenTelemetryScope | None = None
#         self._scopes: dict[str, OpenTelemetryScope] = {}

#     def record(
#         self,
#         scope: ScopeIdentifier,
#         /,
#         metric: State,
#     ) -> None:
#         """Record a metric in the current scope.

#         Args:
#             scope: The scope identifier where the metric is recorded
#             metric: The metric state to record
#         """
#         assert self._root_scope is not None  # nosec: B101
#         assert scope.scope_id in self._scopes  # nosec: B101

#         self._scopes[scope.scope_id].record(
#             str(type(metric)),
#             attributes=metric.as_dict(),
#         )

#         # # Extract metric information and process it
#         # metric_info = self._extract_metric_info(metric, scope)
#         # if not metric_info:
#         #     return  # Skip if no valid metric info

#         # metric_type = metric_info["type"]
#         # metric_name = metric_info["name"]
#         # description = metric_info["description"]
#         # unit = metric_info["unit"]
#         # attributes = metric_info["attributes"]
#         # value = metric_info["value"]

#         # # Record metric based on its type
#         # self._record_metric_by_type(metric_type, metric_name, description, unit, value, attributes)  # noqa: E501

#         # # Update span and scope data if scope exists
#         # self._update_span_for_metric(scope, metric_name, metric_type, value, attributes)

#     # def _extract_metric_info(self, metric: State, scope: ScopeIdentifier) -> dict[str, Any] | None:  # noqa: E501
#     #     """Extract information from a metric State object.

#     #     Args:
#     #         metric: The metric state to extract information from
#     #         scope: The current scope

#     #     Returns:
#     #         Dictionary with metric information or None if invalid
#     #     """
#     #     try:
#     #         # Extract using attribute access
#     #         metric_type = getattr(metric, "type", None)
#     #         if not metric_type:
#     #             return None  # Skip if no type

#     #         metric_name = getattr(metric, "name", "unnamed_metric")

#     #         # Set up scope attributes
#     #         scope_id = getattr(scope, "id", "unknown")
#     #         scope_parent_id = getattr(scope, "parent_id", "unknown")
#     #         attributes = {
#     #             "scope.id": str(scope_id),
#     #             "scope.parent_id": str(scope_parent_id),
#     #         }

#     #         # Add custom attributes
#     #         metric_attrs = getattr(metric, "attributes", {})
#     #         if metric_attrs:
#     #             for key, value in metric_attrs.items():
#     #                 attributes[key] = str(value)

#     #         description = getattr(metric, "description", "")
#     #         unit = getattr(metric, "unit", "1")
#     #         value = self._get_value_from_state(metric)

#     #         return {
#     #             "type": metric_type,
#     #             "name": metric_name,
#     #             "description": description,
#     #             "unit": unit,
#     #             "attributes": attributes,
#     #             "value": value,
#     #         }

#     #     except (AttributeError, TypeError):
#     #         return None

#     # def _record_metric_by_type(
#     #     self,
#     #     metric_type: str,
#     #     metric_name: str,
#     #     description: str,
#     #     unit: str,
#     #     value: float | None,
#     #     attributes: dict[str, str],
#     # ) -> None:
#     #     """Record a metric based on its type.

#     #     Args:
#     #         metric_type: The type of metric (counter, gauge, histogram)
#     #         metric_name: The name of the metric
#     #         description: Human-readable description
#     #         unit: Unit of measurement
#     #         value: The metric value
#     #         attributes: Metric attributes
#     #     """
#     #     if metric_type == "counter":
#     #         counter = self._get_counter(metric_name, description, unit)
#     #         counter.add(value if value is not None else 1, attributes=attributes)

#     #     elif metric_type == "gauge":
#     #         # OpenTelemetry doesn't have a direct gauge concept in the API
#     #         # For simplicity, we're using an UpDownCounter which allows both
#     #         # positive and negative values
#     #         up_down = self._get_up_down_counter(metric_name, description, unit)
#     #         up_down.add(value if value is not None else 0, attributes=attributes)

#     #     elif metric_type == "histogram":
#     #         histogram = self._get_histogram(metric_name, description, unit)
#     #         histogram.record(value if value is not None else 0, attributes=attributes)

#     # def _update_span_for_metric(
#     #     self,
#     #     scope: ScopeIdentifier,
#     #     metric_name: str,
#     #     metric_type: str,
#     #     value: float | None,
#     #     attributes: dict[str, str],
#     # ) -> None:
#     #     """Update the span with metric events and store metric data.

#     #     Args:
#     #         scope: The scope identifier
#     #         metric_name: Name of the metric
#     #         metric_type: Type of metric
#     #         value: Metric value
#     #         attributes: Metric attributes
#     #     """
#     #     scope_id_str = str(getattr(scope, "id", "unknown"))
#     #     if scope_id_str in self._scope_data and "span" in self._scope_data[scope_id_str]:
#     #         span = self._scope_data[scope_id_str]["span"]

#     #         # Add event to trace for this metric
#     #         # Filter None values since they're not valid attribute values in OTel
#     #         event_attrs = {
#     #             "metric.type": metric_type,
#     #             "metric.value": str(value if value is not None else "null"),
#     #         }
#     #         # Add other attributes, ensuring None values are converted to strings
#     #         for k, v in attributes.items():
#     #             event_attrs[k] = str(v) if v is not None else "null"

#     #         span.add_event(
#     #             name=f"metric.{metric_name}",
#     #             attributes=event_attrs,
#     #         )

#     #         # Store metric in scope data
#     #         if "metrics" not in self._scope_data[scope_id_str]:
#     #             self._scope_data[scope_id_str]["metrics"] = []
#     #         self._scope_data[scope_id_str]["metrics"].append(
#     #             {"name": metric_name, "type": metric_type, "value": value, "attributes": attributes}  # noqa: E501
#     #         )

#     def enter_scope(
#         self,
#         scope: ScopeIdentifier,
#         /,
#     ) -> None:
#         """Enter a new metrics scope.

#         Args:
#             scope: The scope identifier to enter
#         """
#         assert scope.scope_id not in self._scopes  # nosec: B101
#         telemetry_scope: OpenTelemetryScope

#         if self._root_scope is None:
#             self._tracer = trace.get_tracer(scope.trace_id)
#             telemetry_scope = OpenTelemetryScope(
#                 scope,
#                 span=self._tracer.start_span(
#                     name=scope.label,
#                     context=Context(
#                         trace_id=scope.trace_id,
#                     ),
#                     attributes={},
#                 ),
#             )

#             self._root_scope = telemetry_scope

#         else:
#             assert self._tracer is not None  # nosec: B101

#             for key in self._scopes.keys():
#                 if key == scope.parent_id:
#                     telemetry_scope = OpenTelemetryScope(
#                         scope,
#                         span=self._tracer.start_span(
#                             name=scope.label,
#                             context=Context(
#                                 # TODO: FIXME: verify context keys for parent span
#                                 span=self._scopes[key].span,
#                                 trace_id=scope.trace_id,
#                             ),
#                             attributes={},
#                         ),
#                     )
#                     self._scopes[key].nested.append(telemetry_scope)
#                     break

#             else:
#                 raise RuntimeError(
#                     "Attempting to enter nested scope metrics without entering its parent first"
#                 )

#         self._scopes[scope.scope_id] = telemetry_scope

#     def exit_scope(
#         self,
#         scope: ScopeIdentifier,
#         /,
#     ) -> None:
#         """Exit the current metrics scope.

#         Args:
#             scope: The scope identifier to exit
#         """
#         assert self._root_scope is not None  # nosec: B101
#         assert scope.scope_id in self._scopes  # nosec: B101

#         self._scopes[scope.scope_id].allow_exit = True

#         if not all(nested.exited for nested in self._scopes[scope.scope_id].nested):
#             return  # not completed yet

#         self._scopes[scope.scope_id].finish()

#         self._scopes[scope.scope_id].exited = monotonic()
#         if scope != self._root_scope and self._scopes[scope.parent_id].allow_exit:
#             self.exit_scope(self._scopes[scope.parent_id].identifier)

#         # # Get the span
#         # span = scope_data.get("span")
#         # if not span:
#         #     return
#         # # Create an end event counter
#         # metric_state = State()
#         # self._set_state_value(metric_state, "type", "counter")
#         # self._set_state_value(metric_state, "name", "scope.end")
#         # self._set_state_value(metric_state, "description", "Scope end event")
#         # self._set_state_value(metric_state, "value", 1)
#         # self._set_state_value(metric_state, "attributes", {"is_root": str(is_root)})

#         # # Record the end metric without using a context manager
#         # self.record(scope, metric_state)

#         # # Calculate duration
#         # start_time = scope_data.get("start_time", monotonic())
#         # end_time = monotonic()
#         # duration_s = end_time - start_time
#         # duration_ms = duration_s * 1000  # Convert to milliseconds

#         # # Record duration as a histogram
#         # duration_metric = State()
#         # self._set_state_value(duration_metric, "type", "histogram")
#         # self._set_state_value(duration_metric, "name", "scope.duration")
#         # self._set_state_value(duration_metric, "description", "Scope duration in milliseconds")
#         # self._set_state_value(duration_metric, "unit", "ms")
#         # self._set_state_value(duration_metric, "value", duration_ms)
#         # metrics_count = len(scope_data.get("metrics", []))
#         # self._set_state_value(
#         #     duration_metric,
#         #     "attributes",
#         #     {"is_root": str(is_root), "metrics_count": str(metrics_count)},
#         # )
#         # self.record(scope, duration_metric)

#         # # Mark the span as completed
#         # span.set_status(StatusCode.OK)
#         # span.end()

#         # # Mark this scope as exited
#         # scope_data["exited"] = True
#         # scope_data["end_time"] = end_time

#         # # If this is not a root scope and the parent is allowed to exit, exit the parent too
#         # parent_id = scope_data.get("parent_id")
#         # if parent_id and parent_id != scope_id_str and parent_id in self._scope_data:
#         #     parent_data = self._scope_data[parent_id]
#         #     if parent_data.get("allow_exit", False):
#         #         # Recreate a ScopeIdentifier with the required fields
#         #         parent_scope = ScopeIdentifier(
#         #             scope_id=parent_id,
#         #             parent_id=parent_data.get("parent_id", parent_id),
#         #             trace_id="",
#         #             label=parent_data.get("label", "parent_scope"),
#         #         )
#         #         self.exit_scope(parent_scope)
