# AI_LOG

> Drafted by the AI assistant at my request from our working session, then reviewed and edited by
> me. The decisions below are mine unless stated otherwise.
> Conversation: <link to the AI conversation>

## How I used AI

- **Tool and models.** Claude Code (Claude Opus 5.5 as the main session; Claude Sonnet 5 and Haiku
  4.5 for subagents doing mechanical or well-specified work).
- **Roles.** The AI acted as a pair programmer and planner (specs, plans, decision log), as an
  orchestrator of implementation subagents (one task at a time), and as a pool of independent,
  adversarial reviewers. I set the rules, made every product and architecture decision, approved
  specs, plans, labels and commits, and ran the final clean-clone check myself.
- **Rules I gave it up front.** The case PDF is the source of truth and stays out of the repo; never
  translate the contract; English everywhere except the contract and the test messages; KISS,
  files under 300 lines, minimal dependencies; tests never call the real model API; no "done"
  without evidence; propose commits, I approve.

## Workflow

Per spec: brainstorm → written spec (with ASCII flow/class/state diagrams and a gaps register) →
spec-gate review → implementation plan → plan-gate review → implementation with one subagent per
task and a review after each task → implementation-gate review → fix wave → scoped re-review →
pull request. Everything is traceable in `docs/MASTER.md` (requirements, decisions D1–D31, gaps
G1–G35) and `docs/superpowers/` (specs, plans, review reports).

Specs 01–02 ran with full rigor (several parallel reviewers per gate; the plan gate executed the
plan verbatim in scratch worktrees and mutation-tested it). From Spec 03 on I reduced rigor to meet
the deadline: one reviewer per gate, cheaper models for mechanical tasks, then fast mode for
Spec 04–06 (no plan gate, one final reviewer). I preferred something working and delivered over
something perfect and late.

## Five key moments

### 1. LangGraph is the harness, not the Agent SDK
- **Asked:** a triage classifier with structured output that could use either an API key or my
  Claude OAuth token.
- **Returned:** at one point the AI leaned toward letting the Claude Agent SDK drive the call.
- **Changed:** I corrected it: LangGraph is always the harness (`call_llm → validate → retry`);
  provider SDKs are only transports behind an adapter interface (`anthropic_api`,
  `claude_agent_sdk`, `mock`), and structured output comes from a forced tool call.
- **Verified:** tests pin the graph flow, the adapter boundary and the exact request body sent to
  the Messages API (temperature 0, forced tool choice, strict schema).

### 2. Labels drafted by AI, decided by me
- **Asked:** draft the 12 expected labels for the case messages, and later an 18-message edge set
  with draft labels.
- **Returned:** complete label tables with a one-line justification for doubtful cases; a
  reviewer then flagged seven edge labels that contradicted the prompt rubric or my own case
  labels, and they were realigned.
- **Changed:** I approved the case labels, approved 16 of 18 edge labels and changed two: EDGE-15
  to `backend` (application behavior, not infrastructure) and EDGE-16 to `otro` with a follow-up
  question. I also asked for `area_sugerida = otro`, which the contract enum does not allow; the AI
  pointed that out and kept `producto`.
- **Verified:** label files validate against the contract enums; golden texts never appear in the
  prompts (tested); README and this log disclose that the labels are AI-drafted and candidate-
  approved.

### 3. No spending on personal credentials
- **Asked:** after the core was built, I decided no real model call would be made with my key.
- **Returned:** a plan to keep the deliverables honest without real calls: build and test every
  tool with the mock provider; promote `resultados.json` from the mock case run only with an
  explicit `--allow-mock` flag and `mock: true` in its metadata; never let a mock result replace a
  real one; document how to regenerate everything with Pitz's key.
- **Changed:** I accepted it and asked that the README say up front that `resultados.json` comes
  from a mock run and exactly how to activate the real model.
- **Verified:** a test pins the committed `resultados.json` to the current prompt, tool schema and
  inputs; the gaps that need a real run (prompt iteration, threshold, calibration, live schema
  acceptance) are listed as G35/G23 and in DECISIONES as verification debt.

### 4. Adversarial reviews that executed the plan
- **Asked:** run independent reviewers at the spec, plan and implementation gates.
- **Returned:** reviewers that applied the plan verbatim in scratch worktrees, ran mutation tests
  and real Docker/uvicorn probes. They caught, before or right after the code existed: a method
  named `list` that broke the import of the repository class, a migration runner that could apply
  half a migration (`executescript` commits first), an app factory incompatible with
  `uvicorn --factory`, PII masking leaks (phones in Markdown italics, invisible Unicode, CPF with
  a wrong separator), a thread-pool starvation that broke the "503 busy" bound, and Slack headers
  that returned 500 instead of 401.
- **Changed:** each finding went through one fix wave and a scoped re-review; rulings were
  recorded in the decision log.
- **Verified:** I re-ran the reported reproductions after each fix; 790 API tests and 56 web tests
  pass; CI is green.

### 5. The AI's own mistakes, caught
- **Asked:** move fast in the last phases.
- **Returned:** a few mistakes: a GitHub Action pinned to a tag that does not exist
  (`setup-uv@v10`), a wrong gap id cited in DECISIONES, a 414-line test file, README claims that
  overstated behavior, and a mock-only comparison command that failed when actually run.
- **Changed:** every one was found by an independent reviewer or by running the thing, then fixed;
  actions are now pinned to full versions and a test enforces it.
- **Verified:** I ran the final clean-clone check myself: `docker compose up` in mock mode,
  `/health`, `make smoke` → `SMOKE OK`, 790 tests, `make eval` with the MOCK RUN banner,
  `docker compose down -v`.

## What I would not delegate again

- **Final verification.** A green report from an agent is a claim; I only trust what I (or a
  separate reviewer) ran. The clean-clone run stays mine.
- **Labels and anything that defines "correct".** The AI can draft them, but the decision has to
  be mine; drafting and judging with the same model is circular.
- **Decisions about cost and credentials.** Whether to spend money, and with which key, is not a
  technical call.
- **Scope under time pressure.** The AI will keep adding rigor; deciding when "good enough and
  delivered" beats "perfect and late" is my job.
