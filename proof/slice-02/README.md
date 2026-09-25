# RawTree trace acceptance

Proof question: does a redacted Pacific Gym development trace survive an insert and a run-ID query with every serialized field unchanged?

`sh scripts/accept-rawtree-trace.sh` builds a representative replay from the real slice 1 inspection report, runs local tests, inserts one row with RawTree's [table API](https://rawtree.com/blog/introducing-rawtree), then uses its [SQL query API](https://rawtree.com/blog/introducing-rawtree) to retrieve that row. The canonical `trace_json` and SHA-256 must match exactly. The query is bounded to two rows so a duplicate run ID fails acceptance.

The replay includes a prompt, tool input, tool output, decision, capture timing, source/report hashes, and artifact URIs. The original inspection duration was not recorded and is explicitly `null`. Original source media bytes never enter the RawTree payload. Credential-like fields, bearer tokens, known environment secret values, and token query parameters are redacted before transmission.

The checked-in `proof.json` currently records `blocked_missing_rawtree_api_key`. Its run ID and SQL are local plans; request IDs and returned row are `null` because no live request was sent. To complete the live gate, provide `RAWTREE_API_KEY` or run with `--api-key-file /absolute/path/to/key`, then commit the updated proof after inspecting it for sensitive content. A passing proof records request IDs, both request durations, the returned row, and the exact trace digest. A local test pass is not a live export.
