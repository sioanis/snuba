from typing import Mapping, Iterable

from snuba.clickhouse.columns import ColumnSet
from snuba.query.expressions import (
    Column,
    Expression,
    FunctionCall,
    Literal,
    NestedColumn,
)
from snuba.query.dsl import array_element
from snuba.query.query import Query
from snuba.query.query_processor import QueryProcessor
from snuba.request.request_settings import RequestSettings


class SingleTagProcessor(QueryProcessor):
    """
    Processes NestedColumns that represent tags or contexts (or any dictionary style
    nested column) into an expression clickhouse understands.
    The nested column must be defined in the form of:
    `Nested([("key", String()), ("value", String())])`
    With a key called `key` and a value called `value`.
    It supports promoted tags/contexts as well.
    """

    def __init__(
        self,
        nested_column_names: Iterable[str],
        columns: ColumnSet,
        promoted_columns: Mapping[str, Iterable[str]],
        key_column_map: Mapping[str, Mapping[str, str]],
    ) -> None:
        # Keeps the names of the nested columns to expand
        self.__nested_column_names = nested_column_names
        # The ColumnSet of the dataset. Used to format promoted
        # columns with the right type.
        self.__columns = columns
        # Keeps a dictionary of promoted columns. The key of the mapping
        # can be any of the nested column names above. The values is a set
        # of flattened columns.
        self.__promoted_columns = promoted_columns
        # Keeps a dictionary of the mapping between promoted keys in the
        # nested columns and the related promoted column
        self.__key_column_map = key_column_map

    def process_query(self, query: Query, request_settings: RequestSettings) -> None:
        def process_column(exp: Expression) -> Expression:
            if (
                not isinstance(exp, NestedColumn)
                or exp.column_name not in self.__nested_column_names
            ):
                return exp

            alias = exp.alias
            key_name = exp.key
            col_name = exp.column_name
            if col_name in self.__promoted_columns:
                promoted_column_name = self.__key_column_map[col_name].get(
                    key_name, key_name
                )
                if promoted_column_name in self.__promoted_columns[col_name]:
                    col_type = self.__columns.get(promoted_column_name, None)
                    col_type = str(col_type) if col_type else None

                    if (
                        col_type
                        and "String" in col_type
                        and "FixedString" not in col_type
                    ):
                        return Column(alias, promoted_column_name, None)
                    else:
                        return FunctionCall(
                            alias,
                            "toString",
                            (Column(None, promoted_column_name, None),),
                        )

            # For the rest, return an expression that looks it up in the nested column itself.
            return array_element(
                alias,
                Column(None, f"{col_name}.value", None),
                FunctionCall(
                    None,
                    "indexOf",
                    (Column(None, f"{col_name}.key", None), Literal(None, key_name)),
                ),
            )

        query.transform_expressions(process_column)
