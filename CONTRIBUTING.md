# Contributing to ha-xtool-s1

Thanks for your interest in contributing! This project is a Home Assistant
custom integration for the xTool S1 laser engraver.

## Getting started

1. Fork the repository and clone it locally
2. Set up the dev environment (WSL Ubuntu recommended):
   ```bash
   python3.13 -m venv ~/venvs/ha-xtool-s1
   source ~/venvs/ha-xtool-s1/bin/activate
   pip install -r requirements_test.txt
   ```
3. Run the test suite:
   ```bash
   pytest -n auto
   ```

## Code standards

- **Formatter**: [Black](https://github.com/psf/black) (line length 88)
- **Linter**: [Ruff](https://github.com/astral-sh/ruff)
- **Test coverage**: 100% required (enforced by CI)
- **Python**: 3.13+
- **Language**: all code, comments and commit messages in English

Run before submitting:
```bash
black custom_components tests
ruff check custom_components tests
pytest -n auto --cov-fail-under=100
```

## Pull requests

- One feature or fix per PR
- Include tests for new functionality
- Update translations (en + de) if adding user-facing strings
- Update `icons.json` if adding new entities
- Keep PRs small and focused

## Reporting bugs

Open an issue with:
- Your HA version and integration version
- Steps to reproduce
- Relevant log output (Settings > System > Logs)
- Diagnostics export if applicable (Settings > Devices > xTool S1 > Download diagnostics)

## Protocol research

If you have access to an xTool S1 and Wireshark, protocol captures are
very welcome. See `docs/PROTOCOL.md` for the current state of
reverse-engineering. Open questions are listed in the "Open questions"
section at the end.

## License

By contributing you agree that your contributions will be licensed under
the [MIT License](./LICENSE).
