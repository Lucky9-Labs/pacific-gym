# RawTree trace integration

## Row contract

`luckybucky_hackathon` is the RawTree table for Pacific Gym agent traces. RawTree rejects `luckybucky-hackathon`: its table API requires `^[a-zA-Z][a-zA-Z0-9_]{0,63}$`, so the project uses the underscore spelling. Each event row contains `run_id`, `codex_session_id`, `codex_thread_id`, `event_id`, `sequence`, `timestamp`, `event_type`, `agent_output`, `tool_name`, `tool_input`, `tool_result`, `status`, `goal`, and `artifact_refs`, plus capture provenance and a row hash.

`tool_input` and `tool_result` are canonical JSON strings. A live read-back showed that RawTree flattens nested objects into dotted column names, which prevents the original object from round-tripping as one field. Null-valued fields can also be omitted, so unknown durations are stored as the string `unknown`. Artifact references remain a small list of URI/hash objects; media bytes stay outside RawTree.

Agent output and rationale are retained as evidence. The exporter does not assign good/bad or alignment verdicts; Liquid's SQL analysis owns those judgments. Credentials are recursively redacted before insertion.

## Live verification and cleanup boundary

On 2026-09-25, the RawTree API rejected the hyphenated table name with a validation error. The underscore table was then created by ingesting four representative event rows. The first read-back exposed nested-object flattening, so exact verification failed for run `de74d610-35bd-4bfb-9836-341a952b5e71`. A subsequent read-only count found four rows total across one run; those rows are from that verification.

The attempted row cleanup was rejected because `/v1/query` allows read queries only. Broader review of RawTree's official docs and its published `@rawtree/mcp` tool reference confirms that data ingestion is a separate `insert-json` operation, while `run-query` is explicitly read-only. The documented table operations include `delete-table`, which removes the whole table and requires admin permission; the reference lists no row-delete operation. So a key that can write/insert data does not thereby make mutation SQL available through the query API. See [RawTree's API overview](https://rawtree.com/docs), [the published MCP tool reference](https://www.npmjs.com/package/%40rawtree/mcp), and [the API example showing separate insert and read-only SQL query operations](https://rawtree.com/blog/introducing-rawtree).

We did not drop the table. The four verification rows were reported in the prior live read and are still the only rows identified for that verification run; the table remains available. Do not claim the post-verification reset or a successful round-trip for the revised JSON-string row contract until a supported row-level deletion path and a fresh read-back are available. The next supported route would need to be confirmed with RawTree (for example, a documented row-delete endpoint or an authorized SQL-console path); do not substitute whole-table deletion.

The local acceptance command records cleanup as unavailable and exits with status 2 after a successful round-trip, rather than sending unsupported mutation SQL or dropping the table. Its current fixture is a representative replay, not a capture of the original inspection timing.
