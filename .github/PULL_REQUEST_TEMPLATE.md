## What does this PR do?

<!-- One or two sentences. What problem does it solve or what feature does it add? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor (behaviour unchanged)
- [ ] Infrastructure / config change
- [ ] Documentation

## TDD checklist

- [ ] Failing test written before implementation (new features and bug fixes)
- [ ] All new code has a corresponding test
- [ ] Existing tests still pass (`pytest app/agents/evaluation/src/tests/ -v`)

## Quality gate

- [ ] `ruff check app/agents/evaluation/src/` — no errors
- [ ] `ruff format --check app/agents/evaluation/src/` — no formatting issues
- [ ] `mypy app/agents/evaluation/src/` — no type errors

## Security

- [ ] No secrets, API keys, or credentials in code or config files
- [ ] All new config values come from environment variables or `config.yaml` (non-secret operational defaults only)
- [ ] All data crossing module boundaries validated through Pydantic models

## Documentation

- [ ] `CLAUDE.md` updated if architecture, source layout, or key design decisions changed
- [ ] `CODING_GUIDE.md` updated if coding standards changed
- [ ] New Lambda handlers documented in `aws_event_driven_orchestration.md` if applicable
