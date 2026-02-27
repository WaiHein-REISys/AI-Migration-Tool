# Guardrail Rules

Guardrail rules are constraints injected into every LLM conversion prompt. They prevent
the LLM from making changes that exceed the migration scope, break API contracts, or
introduce unverified behaviour.

Rules are defined in `config/rules-config.json` and formatted as a numbered list
injected via the `{rules_text}` placeholder in `prompts/conversion_system_*.txt`.

---

## Enforcement Levels

| Level | Behaviour |
|---|---|
| **Blocking** | Pipeline treats the output as BLOCKED if the rule is violated. The step is skipped; the AMBIGUOUS/BLOCKED note is logged. |
| **Warning** | Violation is logged in the conversion log but the output is still written. |

---

## Rule Reference

### RULE-001 — Preserve API Contracts
**Level:** Blocking

**Rule:** Do not change REST endpoint routes, HTTP methods, request/response field names,
or status codes. The migrated API must be wire-compatible with the legacy API.

**Rationale:** Other systems (frontend, integration tests, external clients) depend on
the exact API surface. Any deviation breaks those callers.

**LLM instruction:** "Do NOT change REST endpoint routes, HTTP methods, or payload field names."

---

### RULE-002 — Preserve UI CSS Class Names
**Level:** Blocking

**Rule:** All CSS class names present in the source HTML/template must appear verbatim
in the converted template. No class renaming, no class removal.

**Rationale:** The legacy application may have shared CSS stylesheets, browser test
selectors, or analytics event bindings that target specific class names.

**LLM instruction:** "Preserve ALL CSS class names from the source HTML template exactly."

---

### RULE-003 — No Business Logic Reinterpretation
**Level:** Blocking

**Rule:** Translate code verbatim. Do not optimise, refactor, simplify, or improve
the business logic. If the legacy code has a bug, the bug must be preserved.

**Rationale:** The migration is a like-for-like translation. Behaviour changes belong in
a separate ticket after the migration is validated.

**LLM instruction:** "Do NOT optimize, refactor, or improve logic. Translate VERBATIM."

---

### RULE-004 — Flag Ambiguous Mappings
**Level:** Blocking

**Rule:** If any section of the source cannot be translated with >75% confidence,
respond with:
```
AMBIGUOUS: <clear description of why and what information is needed>
```
Do not guess or produce speculative output.

**Rationale:** Speculative translations silently introduce bugs. Explicit AMBIGUOUS
flags surface items that need human review.

**LLM instruction:** "If you cannot translate ANY section with high confidence, respond with: AMBIGUOUS: <reason>"

---

### RULE-005 — No Out-of-Boundary Changes
**Level:** Blocking

**Rule:** Only convert the source file(s) provided. Do not emit code for other files,
modules, or features not explicitly listed in the approved plan.

**Rationale:** Unplanned file creation breaks the checkpoint/dedup system and may
overwrite work from other migration jobs.

---

### RULE-006 — Log Every Transformation
**Level:** Blocking

**Rule:** Every file conversion must produce a `ConversionLog` entry, including
AMBIGUOUS and BLOCKED outcomes. The log entry must include: source path, target path,
mapping ID, status, and timestamp.

**Rationale:** Full audit trail for review, rollback, and compliance.

---

### RULE-007 — Preserve TypeScript Types
**Level:** Warning

**Rule:** All TypeScript type annotations, interfaces, and generics from the source
must be preserved or have a direct equivalent in the target. Widening types to `any`
is a warning-level violation.

**Rationale:** Type safety is a key benefit of TypeScript. Losing types silently
degrades the codebase quality.

---

### RULE-008 — External Library Halt
**Level:** Blocking

**Rule:** If the source imports from a platform-specific or internal library with no
known target equivalent (e.g. `pfm-*`, `Platform.*`, `@gprs/*`), respond with:
```
BLOCKED: <library name> — no target equivalent found. Manual resolution required.
```

**Rationale:** Platform libraries often have deeply integrated behaviour that cannot
be automatically translated. Forcing the LLM to attempt translation produces incorrect,
silently broken code.

**Common flagged prefixes:** `pfm-`, `Platform.`, `@gprs/`, `GPRS.`, `SolutionBase`

---

### RULE-009 — SQLAlchemy `Mapped[]` Syntax Required
**Level:** Blocking

**Rule:** All SQLAlchemy models must use the 2.0 `Mapped[]` annotation syntax.
The legacy `Column()` syntax without type annotations is not permitted.

**Applies to:** `simpler_grants` target only (uses SQLAlchemy 2.0).

**Rationale:** SQLAlchemy 2.0 `Mapped[]` is type-safe and required by the target
codebase's conventions. Mixing old and new syntax causes runtime warnings and
eventual deprecation errors.

**LLM instruction:** "Use SQLAlchemy 2.0 Mapped[] syntax. Do NOT use Column() without type annotations."

---

### RULE-010 — Audit Events Required for Mutations
**Level:** Warning

**Rule:** Any API endpoint or service method that creates, modifies, or deletes data
must emit an audit event. Translated code that lacks audit calls where the legacy code
had them should be flagged in the conversion log.

**Rationale:** The target system has audit requirements. Missing audit events may
create compliance gaps.

---

## Viewing Rules at Runtime

The formatted rules text is visible in the conversion log for each run:
```
logs/<run-id>-conversion-log.md
```

---

## Adding a New Rule

1. Open `config/rules-config.json`
2. Add a new entry to the `guardrails` array:
   ```json
   {
     "id": "RULE-011",
     "name": "No hardcoded configuration",
     "description": "Environment-specific values (URLs, secrets, timeouts) must use environment variables.",
     "enforcement": "warning"
   }
   ```
3. The new rule is automatically injected into `{rules_text}` on the next run.
4. Optionally update the plan system prompts to reference `RULE-011` by ID.

No Python changes required.

---

## Editing a Rule

Open `config/rules-config.json` and edit the `description` field. The updated text
is used immediately on the next run.

> **Caution:** Changing `"enforcement": "blocking"` to `"warning"` for rules like
> RULE-001 or RULE-003 can cause the pipeline to silently accept incorrect translations.
> Only downgrade enforcement after careful team review.
