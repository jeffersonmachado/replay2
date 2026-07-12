from __future__ import annotations

from pathlib import Path


_COMPILED_FALLBACK_SUFFIXES = {".prg", ".dbo"}
_PREFERENCE_RANK = {
    ".prg": 0,
    ".src": 0,
    ".sql": 0,
    ".dbo": 1,
}


def collect_preferred_source_files(source_dir: str | Path, extensions: set[str]) -> list[Path]:
    """Coleta fontes preferindo texto legível a compilados com o mesmo stem."""
    base = Path(source_dir)
    normalized = {ext.lower() for ext in extensions}

    if base.is_file():
        return [base] if base.suffix.lower() in normalized else []
    if not base.exists():
        return []

    selected: dict[str, Path] = {}
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in normalized:
            continue

        if suffix in _COMPILED_FALLBACK_SUFFIXES and normalized & _COMPILED_FALLBACK_SUFFIXES:
            key = str(path.with_suffix(""))
        else:
            key = str(path)

        current = selected.get(key)
        if current is None:
            selected[key] = path
            continue

        current_rank = _PREFERENCE_RANK.get(current.suffix.lower(), 99)
        new_rank = _PREFERENCE_RANK.get(suffix, 99)
        if new_rank < current_rank:
            selected[key] = path

    return sorted(selected.values())
