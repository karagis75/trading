# Trading Repository Skills

## Python

- Use Python 3 and keep scanner logic small, deterministic, and testable.
- Reuse shared helpers before adding new ticker, pricing, or history logic.
- Keep network access behind clear boundaries so tests can use fixtures or mocks.

## JavaScript

- Preserve the existing Node/browser-compatible style used by the scanner scripts.
- Validate external data before calculations and report actionable errors.

## Testing

- Add or update focused `pytest` tests for changed scanner behavior.
- Run the relevant test module first, then the full test suite when practical.
- Avoid committing generated output, credentials, or local database files.
