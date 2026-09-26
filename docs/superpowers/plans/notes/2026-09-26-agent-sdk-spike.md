# Agent SDK spike (Task 10, Step 1)

No network calls; introspection only, against the installed `claude-agent-sdk` package
(`apps/api/.venv/lib/python3.12/site-packages/claude_agent_sdk`, version pinned in
`apps/api/pyproject.toml` as `claude-agent-sdk>=0.2.160,<0.3`).

## Command

```bash
cd apps/api && uv run python - <<'EOF'
import dataclasses, inspect
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultError, ResultMessage
print(sorted(f.name for f in dataclasses.fields(ClaudeAgentOptions)))
print(inspect.signature(ResultError.__init__))
e = ResultError("x", data={"subtype": "error_max_turns", "api_error_status": 529}, exit_code=1)
print(e.subtype, e.api_error_status)
print([f.name for f in dataclasses.fields(ResultMessage)])
print([f.name for f in dataclasses.fields(AssistantMessage)])
EOF
```

## Raw output

```
['add_dirs', 'agents', 'allowed_tools', 'betas', 'can_use_tool', 'cli_path', 'continue_conversation', 'cwd', 'debug_stderr', 'disallowed_tools', 'effort', 'enable_file_checkpointing', 'env', 'extra_args', 'fallback_model', 'fork_session', 'forward_subagent_text', 'hooks', 'include_hook_events', 'include_partial_messages', 'load_timeout_ms', 'max_budget_usd', 'max_buffer_size', 'max_thinking_tokens', 'max_turns', 'mcp_servers', 'model', 'output_format', 'permission_mode', 'permission_prompt_tool_name', 'plugins', 'resume', 'resume_drops_turn', 'resume_session_at', 'sandbox', 'session_id', 'session_store', 'session_store_flush', 'setting_sources', 'settings', 'skills', 'stderr', 'strict_mcp_config', 'system_prompt', 'task_budget', 'thinking', 'tools', 'user', 'verbatim_prompts']
(self, message: str, data: dict[str, typing.Any] | None = None, exit_code: int | None = None)
error_max_turns 529
['subtype', 'duration_ms', 'duration_api_ms', 'is_error', 'num_turns', 'session_id', 'stop_reason', 'total_cost_usd', 'usage', 'result', 'structured_output', 'model_usage', 'permission_denials', 'deferred_tool_use', 'errors', 'api_error_status', 'uuid', 'terminal_reason', 'origin']
['content', 'model', 'parent_tool_use_id', 'error', 'usage', 'message_id', 'stop_reason', 'session_id', 'uuid']
```

## Findings vs. the brief's expectations

- `ClaudeAgentOptions` fields include all of the brief's expected names: `verbatim_prompts`,
  `thinking`, `tools`, `setting_sources`, `skills`, `strict_mcp_config`, `extra_args`, `env`.
  Also present but not used by the adapter: `agents`, `hooks`, `mcp_servers`, `plugins`,
  `permission_mode`, `max_turns`, `cwd`, `model`, `system_prompt`, and many session/telemetry
  fields (`session_id`, `resume`, `fork_session`, `sandbox`, `output_format`, `effort`,
  `task_budget`, `enable_file_checkpointing`, `session_store`, `session_store_flush`,
  `load_timeout_ms`, ...). The adapter only sets the fields the brief names; everything else
  is left at its dataclass default (confirmed below), which is inert (`None`, `False`, or `[]`/`{}`).
- `ResultError.__init__(self, message, data=None, exit_code=None)` matches the brief exactly.
  Constructing `ResultError("x", data={"subtype": "error_max_turns", "api_error_status": 529},
  exit_code=1)` and reading `.subtype` / `.api_error_status` returns the values from `data`
  (`"error_max_turns"`, `529`) as the brief assumes — these are computed properties over the
  `data` dict, not required constructor args.
- `ResultMessage` fields match the test helper's `result(**overrides)` construction: `subtype,
  duration_ms, duration_api_ms, is_error, num_turns, session_id, usage` are the only fields
  without a dataclass default (all required), and the rest (`stop_reason, total_cost_usd,
  result, structured_output, model_usage, permission_denials, deferred_tool_use, errors,
  api_error_status, uuid, terminal_reason, origin`) default to `None`. So the brief's
  `ResultMessage(**values)` calls with just those seven required keys, plus `api_error_status=`
  as an override, construct without a `TypeError`.
- `AssistantMessage` fields match the test helper's `assistant(text, error=None, model=...)`
  construction: `content, model` are required; `parent_tool_use_id, error, usage, message_id,
  stop_reason, session_id, uuid` all default to `None`. So `AssistantMessage(content=[...],
  model=..., error=...)` constructs without extra required args.
- `CLINotFoundError(message="Claude Code not found", cli_path=None)` and
  `ProcessError(message, exit_code=None, stderr=None)` both accept the brief's test-helper
  call shapes (`CLINotFoundError("missing")`, `ProcessError(f"failed {SENTINEL}", exit_code=1,
  stderr=SENTINEL)`) without extra required args. `CLIConnectionError.__init__` is `(self, /,
  *args, **kwargs)` (inherited, no fixed signature), so `CLIConnectionError("down")` also
  constructs fine.

## Conclusion

No difference from what the brief assumes. Proceeding with Step 2 (failing test) and Step 4
(implementation) as written in the brief, verbatim — no test-helper changes were needed for
extra required constructor arguments.
