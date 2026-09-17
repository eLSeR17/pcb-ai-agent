# Security

## Reporting a vulnerability

This is a public portfolio project. If you find a security issue, please open a
private report via **GitHub Security Advisories** →
[Report a vulnerability](https://github.com/eLSeR17/pcb-ai-agent/security/advisories/new)
instead of a public issue. Once the report is triaged we will coordinate the
disclosure and a fix.

## Local-first threat model

This project runs **entirely on the developer's machine** — there is no hosted
server, no cloud API and no data leaves the local network. The table below lists
the main threat categories and how each is mitigated.

| Threat | Mitigation |
|---|---|
| **LLM hallucination in design answers** | Anti-hallucination grounding layer (`validate_answer()`) checks every component reference and numeric value against tool results *before* the answer is returned. The LLM decides; the code calculates. |
| **Invented numeric values (sizing)** | All sizing formulas (`led_series_resistor`, `pull_up_resistor`, `voltage_divider`, `decoupling_capacitor`) are pure Python functions in `pcbai.design.sizing`. The LLM never computes — it calls the tool, and the tool returns E12-rounded, physically grounded values. |
| **Fabricated firmware code** | Firmware generation uses 8 deterministic templates (`pcbai.firmware.templates`). The LLM picks a template and parameters; the code renders the source. A static validator checks structural integrity. Every artifact carries `requires_human_review=True`. |
| **MCP server exposure** | The MCP server is **read-only** by design (7 tools, none writes to disk or opens network connections). It communicates over stdin/stdout — no HTTP endpoint, no listening socket. The `mcp` SDK is imported lazily and is an optional dependency. |
| **Secrets in the repository** | `.gitignore` excludes `.env`, `.env.local` and all `.env.*` variants (except `.env.example`). The `.env.example` file contains only placeholders. No API keys, tokens or credentials are committed. |
| **Dependency vulnerabilities** | The runtime package is **stdlib-only** (zero external dependencies). Dev/CI dependencies (`pytest`, `ruff`) are pinned with bounded ranges in `requirements.txt`. `pip-audit` runs in CI on every push/PR. |
| **Parsing untrusted KiCad files** | The S-expression parser (`sexpr.py`) is a self-contained, dependency-free implementation. It raises structured errors (`SExprError`, `NetlistError`, `SchematicError`) on malformed input and never executes embedded content. Fixtures are committed; no third-party KiCad files are pulled at runtime. |

## Dependabot / pip-audit policy

- **pip-audit** runs in CI on every push and pull request, scanning the
  declared dependency tree (`requirements.txt`).
- **Dependabot** (GitHub-native) monitors `pip` and `github-actions` dependency
  groups. Alerts are triaged against how this repository *actually uses* each
  dependency — not against the full upstream product surface.
- Dev-only dependencies (`pytest`, `ruff`) that have known CVEs in test-runner
  contexts are documented and excluded only when the vulnerability does not
  affect the project's attack surface. Exceptions are always accompanied by a
  comment in `ci.yml`.
