import logging
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, MutableMapping, Optional, Sequence, Tuple, TypedDict

from snuba import settings
from snuba.consumers.types import KafkaMessageMetadata
from snuba.datasets.events_format import (
    EventTooOld,
    enforce_retention,
    extract_extra_contexts,
    extract_extra_tags,
    extract_project_id,
)
from snuba.processor import (
    InsertBatch,
    InvalidMessageType,
    InvalidMessageVersion,
    MessageProcessor,
    ProcessedMessage,
    ReplacementBatch,
    _as_dict_safe,
    _boolify,
    _collapse_uint32,
    _ensure_valid_date,
    _unicodify,
)

logger = logging.getLogger(__name__)


class ReplacementType(str, Enum):
    START_DELETE_GROUPS = "start_delete_groups"
    START_MERGE = "start_merge"
    START_UNMERGE = "start_unmerge"
    START_UNMERGE_HIERARCHICAL = "start_unmerge_hierarchical"
    START_DELETE_TAG = "start_delete_tag"
    END_DELETE_GROUPS = "end_delete_groups"
    END_MERGE = "end_merge"
    END_UNMERGE = "end_unmerge"
    END_UNMERGE_HIERARCHICAL = "end_unmerge_hierarchical"
    END_DELETE_TAG = "end_delete_tag"
    TOMBSTONE_EVENTS = "tombstone_events"
    REPLACE_GROUP = "replace_group"
    EXCLUDE_GROUPS = "exclude_groups"


REPLACEMENT_EVENT_TYPES = frozenset(
    [
        ReplacementType.START_DELETE_GROUPS,
        ReplacementType.START_MERGE,
        ReplacementType.START_UNMERGE,
        ReplacementType.START_DELETE_TAG,
        ReplacementType.END_DELETE_GROUPS,
        ReplacementType.END_MERGE,
        ReplacementType.END_UNMERGE,
        ReplacementType.END_DELETE_TAG,
        ReplacementType.TOMBSTONE_EVENTS,
        ReplacementType.EXCLUDE_GROUPS,
        ReplacementType.REPLACE_GROUP,
    ]
)


class InsertEvent(TypedDict):
    group_id: Optional[int]
    event_id: str
    organization_id: int
    project_id: int
    message: str
    platform: str
    datetime: str  # snuba.settings.PAYLOAD_DATETIME_FORMAT
    data: MutableMapping[str, Any]
    primary_hash: str  # empty string represents None
    retention_days: int


class EventsProcessorBase(MessageProcessor, ABC):
    """
    Base class for events and errors processors.
    """

    @abstractmethod
    def _should_process(self, event: InsertEvent) -> bool:
        raise NotImplementedError

    @abstractmethod
    def _extract_event_id(
        self, output: MutableMapping[str, Any], event: InsertEvent,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def extract_custom(
        self,
        output: MutableMapping[str, Any],
        event: InsertEvent,
        metadata: KafkaMessageMetadata,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def extract_promoted_tags(
        self, output: MutableMapping[str, Any], tags: Mapping[str, Any],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def extract_tags_custom(
        self,
        output: MutableMapping[str, Any],
        event: InsertEvent,
        tags: Mapping[str, Any],
        metadata: KafkaMessageMetadata,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def extract_promoted_contexts(
        self,
        output: MutableMapping[str, Any],
        contexts: Mapping[str, Any],
        tags: Mapping[str, Any],
    ) -> None:
        raise NotImplementedError

    def extract_required(
        self, output: MutableMapping[str, Any], event: InsertEvent,
    ) -> None:
        output["group_id"] = event["group_id"] or 0

        # This is not ideal but it should never happen anyways
        timestamp = _ensure_valid_date(
            datetime.strptime(event["datetime"], settings.PAYLOAD_DATETIME_FORMAT)
        )
        if timestamp is None:
            timestamp = datetime.utcnow()

        output["timestamp"] = timestamp

    def extract_sdk(
        self, output: MutableMapping[str, Any], sdk: Mapping[str, Any]
    ) -> None:
        output["sdk_name"] = _unicodify(sdk.get("name", None))
        output["sdk_version"] = _unicodify(sdk.get("version", None))

        sdk_integrations = []
        for i in sdk.get("integrations", None) or ():
            i = _unicodify(i)
            if i:
                sdk_integrations.append(i)
        output["sdk_integrations"] = sdk_integrations

    def process_message(
        self,
        message: Tuple[int, str, InsertEvent, Any],
        metadata: KafkaMessageMetadata,
    ) -> Optional[ProcessedMessage]:
        """\
        Process a raw message into an insertion or replacement batch. Returns
        `None` if the event is too old to be written.
        """
        version = message[0]
        if version != 2:
            raise InvalidMessageVersion(f"Unsupported message version: {version}")

        # version 2: (2, type, data, [state])
        type_, event = message[1:3]
        if type_ == "insert":
            try:
                row = self.process_insert(event, metadata)
            except EventTooOld:
                return None

            if row is None:  # the processor cannot/does not handle this input
                return None

            return InsertBatch([row], None)
        elif type_ in REPLACEMENT_EVENT_TYPES:
            # pass raw events along to republish
            return ReplacementBatch(str(event["project_id"]), [message])
        else:
            raise InvalidMessageType(f"Invalid message type: {type_}")

    def process_insert(
        self, event: InsertEvent, metadata: KafkaMessageMetadata
    ) -> Optional[Mapping[str, Any]]:
        if not self._should_process(event):
            return None

        processed: MutableMapping[str, Any] = {"deleted": 0}
        extract_project_id(processed, event)
        self._extract_event_id(processed, event)
        processed["retention_days"] = enforce_retention(
            event,
            datetime.strptime(event["datetime"], settings.PAYLOAD_DATETIME_FORMAT),
        )

        self.extract_required(processed, event)

        data = event.get("data", {})
        # HACK: https://sentry.io/sentry/snuba/issues/802102397/
        if not data:
            logger.error("No data for event: %s", event, exc_info=True)
            return None
        self.extract_common(processed, event, metadata)
        self.extract_custom(processed, event, metadata)

        sdk = data.get("sdk", None) or {}
        self.extract_sdk(processed, sdk)

        tags: Mapping[str, Any] = _as_dict_safe(data.get("tags", None))
        self.extract_promoted_tags(processed, tags)
        self.extract_tags_custom(processed, event, tags, metadata)

        contexts = data.get("contexts", None) or {}
        self.extract_promoted_contexts(processed, contexts, tags)

        processed["contexts.key"], processed["contexts.value"] = extract_extra_contexts(
            contexts
        )
        processed["tags.key"], processed["tags.value"] = extract_extra_tags(tags)

        exception = (
            data.get("exception", data.get("sentry.interfaces.Exception", None)) or {}
        )
        stacks = exception.get("values", None) or []
        self.extract_stacktraces(processed, stacks)

        processed["offset"] = metadata.offset
        processed["partition"] = metadata.partition
        processed["message_timestamp"] = metadata.timestamp

        return processed

    def extract_common(
        self,
        output: MutableMapping[str, Any],
        event: InsertEvent,
        metadata: KafkaMessageMetadata,
    ) -> None:
        # Properties we get from the top level of the message payload
        output["platform"] = _unicodify(event["platform"])

        # Properties we get from the "data" dict, which is the actual event body.
        data = event.get("data", {})
        received = _collapse_uint32(int(data["received"]))
        output["received"] = (
            datetime.utcfromtimestamp(received) if received is not None else None
        )
        output["version"] = _unicodify(data.get("version", None))
        output["location"] = _unicodify(data.get("location", None))

        module_names = []
        module_versions = []
        modules = data.get("modules", {})
        if isinstance(modules, dict):
            for name, version in modules.items():
                module_names.append(_unicodify(name))
                # Being extra careful about a stray (incorrect by spec) `null`
                # value blowing up the write.
                module_versions.append(_unicodify(version) or "")

        output["modules.name"] = module_names
        output["modules.version"] = module_versions

    def extract_stacktraces(
        self, output: MutableMapping[str, Any], stacks: Sequence[Any]
    ) -> None:
        stack_types = []
        stack_values = []
        stack_mechanism_types = []
        stack_mechanism_handled = []

        frame_abs_paths = []
        frame_filenames = []
        frame_packages = []
        frame_modules = []
        frame_functions = []
        frame_in_app = []
        frame_colnos = []
        frame_linenos = []
        frame_stack_levels = []

        if output["project_id"] not in settings.PROJECT_STACKTRACE_BLACKLIST:
            stack_level = 0
            for stack in stacks:
                if stack is None:
                    continue

                stack_types.append(_unicodify(stack.get("type", None)))
                stack_values.append(_unicodify(stack.get("value", None)))

                mechanism = stack.get("mechanism", None) or {}
                stack_mechanism_types.append(_unicodify(mechanism.get("type", None)))
                stack_mechanism_handled.append(_boolify(mechanism.get("handled", None)))

                frames = (stack.get("stacktrace", None) or {}).get("frames", None) or []
                for frame in frames:
                    if frame is None:
                        continue

                    frame_abs_paths.append(_unicodify(frame.get("abs_path", None)))
                    frame_filenames.append(_unicodify(frame.get("filename", None)))
                    frame_packages.append(_unicodify(frame.get("package", None)))
                    frame_modules.append(_unicodify(frame.get("module", None)))
                    frame_functions.append(_unicodify(frame.get("function", None)))
                    frame_in_app.append(frame.get("in_app", None))
                    frame_colnos.append(_collapse_uint32(frame.get("colno", None)))
                    frame_linenos.append(_collapse_uint32(frame.get("lineno", None)))
                    frame_stack_levels.append(stack_level)

                stack_level += 1

        output["exception_stacks.type"] = stack_types
        output["exception_stacks.value"] = stack_values
        output["exception_stacks.mechanism_type"] = stack_mechanism_types
        output["exception_stacks.mechanism_handled"] = stack_mechanism_handled
        output["exception_frames.abs_path"] = frame_abs_paths
        output["exception_frames.filename"] = frame_filenames
        output["exception_frames.package"] = frame_packages
        output["exception_frames.module"] = frame_modules
        output["exception_frames.function"] = frame_functions
        output["exception_frames.in_app"] = frame_in_app
        output["exception_frames.colno"] = frame_colnos
        output["exception_frames.lineno"] = frame_linenos
        output["exception_frames.stack_level"] = frame_stack_levels
