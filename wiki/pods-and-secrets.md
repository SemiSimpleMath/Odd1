# Pods and secrets
- A pod is content passed by reference (`pod:<kind>:<id>`): put it once (`odd1-pods put`, MCP `pod_put`), pass the
  reference in messages and task logs. Kinds are registered in `components/pods/kinds.json`.
- Secrets are pods agents hand on but never read: a person stores them at a terminal (`odd1-secrets set`,
  `odd1-secrets import`), each with where it may go (`host:`, `env:`). Agents use them through the courier:
  `odd1-secrets run --with VAR=pod:... -- program`, `odd1-secrets http`, MCP `pod_http`, `pod_run`; replies come back
  scrubbed. Never paste a key into a chat.
- Details: `components/pods/README.md`.
