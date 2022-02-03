import React, { useState, useEffect } from "react";
import Client from "../api_client";
import { Table } from "../table";
import QueryDisplay from "../components/query_display/query_display";
import { QueryResult } from "../components/query_display/types";

type PredefinedQuery = {
  name?: string;
  sql?: string;
  description?: string;
  selected?: boolean;
};

function ClickhouseQueries(props: { api: Client }) {
  const [predefinedQuery, setPredefinedQuery] = useState<PredefinedQuery>({});
  const [predefinedQueryOptions, setPredefinedQueryOptions] = useState<
    PredefinedQuery[]
  >([]);

  useEffect(() => {
    props.api.getPredefinedQueryOptions().then((res) => {
      res.forEach(
        (queryOption) => (queryOption.sql = formatSQL(queryOption.sql))
      );
      setPredefinedQueryOptions(res);
    });
  }, []);

  function tablePopulator(queryResult: QueryResult) {
    return (
      <Table headerData={queryResult.column_names} rowData={queryResult.rows} />
    );
  }

  function updatePredefinedQuery(queryName: string) {
    const selectedQuery = predefinedQueryOptions.find(
      (query) => query.name === queryName
    );
    setPredefinedQuery(() => {
      return {
        ...selectedQuery,
        selected: false,
      };
    });
  }

  function selectPredefinedQuery() {
    setPredefinedQuery((prevQuery) => {
      return {
        ...prevQuery,
        selected: true,
      };
    });
  }

  function clearPredefinedQuery() {
    setPredefinedQuery(() => {
      return {};
    });
  }

  function formatSQL(sql: string) {
    const formatted = sql
      .split("\n")
      .map((line) => line.substring(4, line.length))
      .join("\n");
    return formatted.trim();
  }

  return (
    <div>
      <div>
        <form>
          <select
            value={predefinedQuery.name || ""}
            onChange={(evt) => updatePredefinedQuery(evt.target.value)}
            style={selectStyle}
          >
            <option disabled value="">
              Select a predefined query
            </option>
            {predefinedQueryOptions.map((option: PredefinedQuery) => (
              <option key={option.name} value={option.name}>
                {option.name}
              </option>
            ))}
          </select>
        </form>
        {predefinedQuery?.sql && (
          <div>
            {
              <div
                style={{
                  fontSize: 14,
                  marginBottom: 5,
                  width: "50vw",
                }}
              >
                <p
                  dangerouslySetInnerHTML={{
                    __html: predefinedQuery.description || "",
                  }}
                ></p>
              </div>
            }
            <textarea
              readOnly={true}
              value={predefinedQuery?.sql}
              spellCheck={false}
              style={{ width: "50vw", height: 100 }}
            />
            <div>
              {predefinedQuery.selected ? (
                <button onClick={clearPredefinedQuery}>Clear</button>
              ) : (
                <button onClick={selectPredefinedQuery}>Use query</button>
              )}
            </div>
          </div>
        )}
      </div>
      {QueryDisplay({
        api: props.api,
        endpoint: "run_clickhouse_system_query",
        resultDataPopulator: tablePopulator,
        predefinedQuery: predefinedQuery?.selected ? predefinedQuery.sql : "",
      })}
    </div>
  );
}

const selectStyle = {
  marginBottom: 8,
  height: 30,
};

export default ClickhouseQueries;
