# Contributing to IranPlate Vision

Thanks for your interest in contributing.

## Development Setup

1. Fork and clone the repository.
2. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

3. Run the app:

```bash
python app.py
```

4. Optional smoke test (app must be running):

```bash
python scripts/smoke_test.py
```

## Branch and Commit Guidelines

- Create a feature branch from `main`.
- Use clear commit messages:
  - `feat: ...`
  - `fix: ...`
  - `docs: ...`
  - `refactor: ...`

## Pull Request Checklist

- Keep PR scope focused and small.
- Update docs if behavior or setup changed.
- Include screenshots for UI changes (`/scan`, `/cameras`) in both EN/FA when relevant.
- Confirm no secrets or local artifacts are included:
  - `cert.pem`, `key.pem`
  - `traffic.db`, `*.db-shm`, `*.db-wal`

## Coding Notes

- Follow existing project style and naming.
- Keep changes backward-compatible unless clearly documented.
- Prefer simple, explicit logic over clever shortcuts.

## Reporting Issues

When opening a bug report, include:

- Steps to reproduce
- Expected vs actual behavior
- Logs/error output
- Environment details (OS, Python version, browser)
- Sample RTSP URL pattern (do not share private credentials)

## Security

If you find a security issue, avoid posting sensitive details publicly in Issues.
Open a private report channel with the maintainer first.
