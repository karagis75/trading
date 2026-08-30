# Agent Instructions

## Before Editing

1. Inspect the existing implementation and its tests.
2. Identify the smallest change that satisfies the request.
3. Check for repository-local conventions and avoid unrelated cleanup.

## Code Changes

- Preserve public behavior unless the task explicitly changes it.
- Reuse existing utilities and data models.
- Add regression coverage when fixing a bug or changing behavior.
- Keep credentials and personal data out of source, tests, and logs.

## Completion Checklist

- Run focused tests, followed by broader tests when the change warrants them.
- Review the diff for accidental files or debug code.
- Use a descriptive commit message and leave the working tree clean.
