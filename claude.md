# Claude Instructions

## Repository Scope

This repository contains Python and JavaScript stock-market scanners, a Flask web
application, scheduler scripts, and scanner-history persistence.

## Implementation Guidance

- Read the relevant scanner, route, helper, and test files before changing behavior.
- Prefer focused changes that preserve existing command-line and web interfaces.
- Keep market-data calls resilient to empty, malformed, or unavailable responses.
- Do not hard-code secrets, tokens, or machine-specific paths.
- Match the surrounding naming, formatting, and error-handling conventions.

## Verification

- Run focused tests for each changed area.
- Run the full `pytest` suite for cross-cutting changes.
- Summarize any tests that could not run and why.
