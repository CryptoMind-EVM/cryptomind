"""Regression checks for import-time syntax failures in API modules."""

from pathlib import Path


def test_all_api_modules_compile():
    api_dir = Path(__file__).resolve().parents[1] / "api"
    failures = []

    for path in api_dir.rglob("*.py"):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            failures.append(
                f"{path.relative_to(api_dir.parent)}:{exc.lineno}: {exc.msg}"
            )

    assert not failures, "\n".join(failures)
