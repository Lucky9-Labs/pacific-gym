# RawTree trace acceptance

Proof question: does a redacted Pacific Gym development trace survive an insert and a run-ID query with every serialized field unchanged?

`sh scripts/accept-rawtree-trace.sh` builds a representative replay from the real slice 1 inspection report, runs local tests, inserts one row with RawTree's [table API](https://rawtree.com/blog/introducing-rawtree), then uses its [SQL query API](https://rawtree.com/blog/introducing-rawtree) to retrieve that row. The canonical `trace_json` and SHA-256 must match exactly. The query is bounded to two rows so a duplicate run ID fails acceptance.

The replay includes a prompt, tool input, tool output, decision, capture timing, source/report hashes, and artifact URIs. The original inspection duration was not recorded and is explicitly `null`. Original source media bytes never enter the RawTree payload. Credential-like fields, bearer tokens, known environment secret values, and token query parameters are redacted before transmission.

The checked-in `proof.json` records a live round trip. It includes the insert and query request IDs, the exact SQL, bounded query attempts, both request durations, the returned row, and the matching trace digest. A development run showed that an immediate query can return zero rows after a confirmed insert, so acceptance retries briefly; the recorded run found its row on the first query. The key's cluster scope was not independently identified by the API response. Local `main` keeps its key in an ignored private file with `.env` symlinked to it; no key is committed.
