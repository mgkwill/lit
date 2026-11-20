# Discover commands and schemas

OpenLit exposes command information as JSON so clients can inspect available
operations instead of parsing help text.

```sh
lit ontology
lit schema
lit schema --command add
```

The schema output uses JSON Schema draft 2020-12. A command entry describes its
inputs and outputs. Use the schema returned by your installed version and verify
the command's actual result; discovery is not proof that every combination of
arguments or every platform is supported.

MCP clients use `tools/list` for tool-specific schemas. Tool names such as
`lit_add`, `lit_status`, and `lit_schema` stay unchanged by the OpenLit rename.
Use `lit mcp-serve --stdio` in the intended repository. The reported package
version is 0.1.0; protocol versions are separate and do not change with branding.

The ontology is maintained in `src/ontology/mod.rs`. When a command changes,
update its CLI, schema, MCP mapping, and behavioral tests together. Avoid listing
planned algorithms or security certifications as implemented capabilities.
See [examples](EXAMPLES.md) and [testing](TESTING.md).
