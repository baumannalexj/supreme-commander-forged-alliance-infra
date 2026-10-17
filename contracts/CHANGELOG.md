# Contracts changelog

Versioning: semantic. Every schema carries `x-contract-version`. Adding optional fields is a
minor bump; removing or retyping a field, or changing a tool's meaning, is a major bump
(while 0.x, any change may break and is listed here).

## 0.1.0

First slice.

- `event.schema.json`: CloudEvents-style envelope (`specversion`, `id`, `source`, `type`, `time`, `subject`, `data`); `type` is `github.<event>[.<action>]`.
- `mcp/event-view.schema.json`: envelope plus store `seq`, as returned by tools.
- MCP tools (read-only), each with `<tool>.input.schema.json` and `<tool>.output.schema.json`:
  `list_events`, `get_event`, `next_events`, `ack`, `stats`.
- `workflows/book-check.outputs.schema.json`: outputs of the `book-check` reusable workflow stub.
