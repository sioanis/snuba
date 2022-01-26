from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

import pytest

from snuba import settings
from snuba.clickhouse.value_types import (
    ClickhouseInt,
    ClickhouseNumArray,
    ClickhouseUnguardedExpression,
)
from snuba.consumers.types import KafkaMessageMetadata
from snuba.datasets.metrics_aggregate_processor import (
    CounterAggregateProcessor,
    DistributionsAggregateProcessor,
    SetsAggregateProcessor,
)
from snuba.datasets.metrics_bucket_processor import (
    CounterMetricsProcessor,
    DistributionsMetricsProcessor,
    SetsMetricsProcessor,
)
from snuba.processor import AggregateInsertBatch, InsertBatch

TEST_CASES_BUCKETS = [
    pytest.param(
        {
            "org_id": 1,
            "project_id": 2,
            "metric_id": 1232341,
            "type": "s",
            "timestamp": 1619225296,
            "tags": {"10": 11, "20": 22, "30": 33},
            "value": [324234, 345345, 456456, 567567],
            "retention_days": 30,
        },
        [
            {
                "org_id": 1,
                "project_id": 2,
                "metric_id": 1232341,
                "timestamp": datetime(2021, 4, 24, 0, 48, 16),
                "tags.key": [10, 20, 30],
                "tags.value": [11, 22, 33],
                "set_values": [324234, 345345, 456456, 567567],
                "materialization_version": 0,
                "retention_days": 30,
                "partition": 1,
                "offset": 100,
            }
        ],
        None,
        None,
        id="Simple set with valid content",
    ),
    pytest.param(
        {
            "org_id": 1,
            "project_id": 2,
            "metric_id": 1232341,
            "type": "c",
            "timestamp": 1619225296,
            "tags": {"10": 11, "20": 22, "30": 33},
            "value": 123.123,
            "retention_days": 30,
        },
        None,
        [
            {
                "org_id": 1,
                "project_id": 2,
                "metric_id": 1232341,
                "timestamp": datetime(2021, 4, 24, 0, 48, 16),
                "tags.key": [10, 20, 30],
                "tags.value": [11, 22, 33],
                "value": 123.123,
                "materialization_version": 0,
                "retention_days": 30,
                "partition": 1,
                "offset": 100,
            }
        ],
        None,
        id="Simple counter with valid content",
    ),
    pytest.param(
        {
            "org_id": 1,
            "project_id": 2,
            "metric_id": 1232341,
            "type": "d",
            "timestamp": 1619225296,
            "tags": {"10": 11, "20": 22, "30": 33},
            "value": [324.12, 345.23, 4564.56, 567567],
            "retention_days": 30,
        },
        None,
        None,
        [
            {
                "org_id": 1,
                "project_id": 2,
                "metric_id": 1232341,
                "timestamp": datetime(2021, 4, 24, 0, 48, 16),
                "tags.key": [10, 20, 30],
                "tags.value": [11, 22, 33],
                "values": [324.12, 345.23, 4564.56, 567567],
                "materialization_version": 0,
                "retention_days": 30,
                "partition": 1,
                "offset": 100,
            }
        ],
        id="Simple set with valid content",
    ),
]


@pytest.mark.parametrize(
    "message, expected_set, expected_counter, expected_distributions",
    TEST_CASES_BUCKETS,
)
def test_metrics_processor(
    message: Mapping[str, Any],
    expected_set: Optional[Sequence[Mapping[str, Any]]],
    expected_counter: Optional[Sequence[Mapping[str, Any]]],
    expected_distributions: Optional[Sequence[Mapping[str, Any]]],
) -> None:
    settings.DISABLED_DATASETS = set()

    meta = KafkaMessageMetadata(offset=100, partition=1, timestamp=datetime(1970, 1, 1))

    expected_set_result = (
        InsertBatch(expected_set, None) if expected_set is not None else None
    )
    assert SetsMetricsProcessor().process_message(message, meta) == expected_set_result

    expected_counter_result = (
        InsertBatch(expected_counter, None) if expected_counter is not None else None
    )
    assert (
        CounterMetricsProcessor().process_message(message, meta)
        == expected_counter_result
    )

    expected_distributions_result = (
        InsertBatch(expected_distributions, None)
        if expected_distributions is not None
        else None
    )
    assert (
        DistributionsMetricsProcessor().process_message(message, meta)
        == expected_distributions_result
    )


def explode_test_cases_with_granularities(
    template: Mapping[str, Any],
    granularities: Sequence[int] = [10, 3600, 86400],
    placeholder: str = "{gran}",
) -> Sequence[Mapping[str, Any]]:
    return [
        {
            **{
                k: ClickhouseUnguardedExpression(
                    v.val.replace(placeholder, str(granularity))
                )
                if isinstance(v, ClickhouseUnguardedExpression)
                else v
                for (k, v) in template.items()
            },
            "granularity": ClickhouseInt(granularity),
        }
        for granularity in granularities
    ]


TEST_CASES_AGGREGATES = [
    pytest.param(
        {
            "org_id": 1,
            "project_id": 2,
            "metric_id": 1232341,
            "type": "s",
            "timestamp": 1619225296,
            "tags": {"10": 11, "20": 22, "30": 33},
            "value": [324234, 345345, 456456, 567567],
            "retention_days": 30,
        },
        explode_test_cases_with_granularities(
            {
                "org_id": ClickhouseInt(1),
                "project_id": ClickhouseInt(2),
                "metric_id": ClickhouseInt(1232341),
                "timestamp": ClickhouseUnguardedExpression(
                    val="toStartOfInterval(toDateTime('2021-04-24T00:48:16'), toIntervalSecond({gran}))"
                ),
                "tags.key": ClickhouseNumArray([10, 20, 30]),
                "tags.value": ClickhouseNumArray([11, 22, 33]),
                "value": ClickhouseUnguardedExpression(
                    val="arrayReduce('uniqState', [324234,345345,456456,567567])"
                ),
                "retention_days": ClickhouseInt(30),
                "partition": 1,
                "offset": 100,
            }
        ),
        None,
        None,
        id="Simple set with valid content",
    )
]


@pytest.mark.parametrize(
    "message, expected_set, expected_counter, expected_distributions",
    TEST_CASES_AGGREGATES,
)
def test_metrics_aggregate_processor(
    message: Mapping[str, Any],
    expected_set: Optional[Sequence[Mapping[str, Any]]],
    expected_counter: Optional[Sequence[Mapping[str, Any]]],
    expected_distributions: Optional[Sequence[Mapping[str, Any]]],
) -> None:
    settings.DISABLED_DATASETS = set()

    meta = KafkaMessageMetadata(offset=100, partition=1, timestamp=datetime(1970, 1, 1))

    expected_set_result = (
        AggregateInsertBatch(expected_set, None) if expected_set is not None else None
    )
    assert (
        SetsAggregateProcessor().process_message(message, meta).rows[0]
        == expected_set_result.rows[0]
    )

    expected_counter_result = (
        AggregateInsertBatch(expected_counter, None)
        if expected_counter is not None
        else None
    )
    assert (
        CounterAggregateProcessor().process_message(message, meta)
        == expected_counter_result
    )

    expected_distributions_result = (
        AggregateInsertBatch(expected_distributions, None)
        if expected_distributions is not None
        else None
    )
    assert (
        DistributionsAggregateProcessor().process_message(message, meta)
        == expected_distributions_result
    )
