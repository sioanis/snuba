from snuba import settings
from snuba.clickhouse.columns import (
    UUID,
    Array,
    ColumnSet,
    DateTime,
    Float,
    LowCardinality,
    Nullable,
    String,
    UInt,
    WithDefault,
)
from snuba.clusters.storage_sets import StorageSetKey
from snuba.datasets.querylog_processor import QuerylogProcessor
from snuba.datasets.schemas.tables import WritableTableSchema
from snuba.datasets.storage import WritableTableStorage
from snuba.datasets.storages import StorageKey
from snuba.datasets.table_storage import KafkaStreamLoader

NESTED_ARRAY_DEFAULT = "arrayResize([['']], length(clickhouse_queries.sql))"

columns = ColumnSet(
    [
        ("request_id", UUID()),
        ("request_body", String()),
        ("referrer", LowCardinality(String())),
        ("dataset", LowCardinality(String())),
        ("projects", Array(UInt(64))),
        ("organization", Nullable(UInt(64))),
        ("timestamp", DateTime()),
        ("duration_ms", UInt(32)),
        ("status", LowCardinality(String())),
        # clickhouse_queries Nested columns.
        # This is expanded into arrays instead of being expressed as a
        # Nested column because, when adding new columns to a nested field
        # we need to provide a default for the entire array (each new column
        # is an array).
        # The same schema cannot be achieved with the Nested construct (where
        # we can only provide default for individual values), so, if we
        # use the Nested construct, this schema cannot match the one generated
        # by the migration framework (or by any ALTER statement).
        ("clickhouse_queries.sql", Array(String())),
        ("clickhouse_queries.status", Array(LowCardinality(String()))),
        ("clickhouse_queries.trace_id", Array(Nullable(UUID()))),
        ("clickhouse_queries.duration_ms", Array(UInt(32))),
        ("clickhouse_queries.stats", Array(String())),
        ("clickhouse_queries.final", Array(UInt(8))),
        ("clickhouse_queries.cache_hit", Array(UInt(8))),
        ("clickhouse_queries.sample", Array(Float(32))),
        ("clickhouse_queries.max_threads", Array(UInt(8))),
        ("clickhouse_queries.num_days", Array(UInt(32))),
        ("clickhouse_queries.clickhouse_table", Array(LowCardinality(String()))),
        ("clickhouse_queries.query_id", Array(String())),
        # XXX: ``is_duplicate`` is currently not set when using the
        # ``Cache.get_readthrough`` query execution path. See GH-902.
        ("clickhouse_queries.is_duplicate", Array(UInt(8))),
        ("clickhouse_queries.consistent", Array(UInt(8))),
        (
            "clickhouse_queries.all_columns",
            WithDefault(Array(Array(LowCardinality(String()))), NESTED_ARRAY_DEFAULT),
        ),
        (
            "clickhouse_queries.or_conditions",
            WithDefault(
                Array(UInt(8)), "arrayResize([0], length(clickhouse_queries.sql))",
            ),
        ),
        (
            "clickhouse_queries.where_columns",
            WithDefault(Array(Array(LowCardinality(String()))), NESTED_ARRAY_DEFAULT),
        ),
        (
            "clickhouse_queries.where_mapping_columns",
            WithDefault(Array(Array(LowCardinality(String()))), NESTED_ARRAY_DEFAULT),
        ),
        (
            "clickhouse_queries.groupby_columns",
            WithDefault(Array(Array(LowCardinality(String()))), NESTED_ARRAY_DEFAULT),
        ),
        (
            "clickhouse_queries.array_join_columns",
            WithDefault(Array(Array(LowCardinality(String()))), NESTED_ARRAY_DEFAULT),
        ),
    ]
)

# Note, we are using the simplified WritableTableSchema class here instead of
# the MergeTreeSchema that corresponds to the actual table engine. This is because
# the querylog table isn't generated by the old migration system.
schema = WritableTableSchema(
    columns=columns,
    local_table_name="querylog_local",
    dist_table_name="querylog_dist",
    storage_set_key=StorageSetKey.QUERYLOG,
)

storage = WritableTableStorage(
    storage_key=StorageKey.QUERYLOG,
    storage_set_key=StorageSetKey.QUERYLOG,
    schema=schema,
    query_processors=[],
    stream_loader=KafkaStreamLoader(
        processor=QuerylogProcessor(), default_topic=settings.QUERIES_TOPIC,
    ),
)
