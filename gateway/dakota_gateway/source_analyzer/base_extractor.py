"""Interface comum dos extractors do source_analyzer (dívida G3).

Antes desta ABC, cada extractor (SQL, ISAM, DBF, Recital, Screen) era
independente e o parser os invocava nominalmente. O contrato comum
formaliza: todo extractor é stateless, expõe ``name`` e implementa o
estático ``extract(content, source_file="") -> list`` (entidades ou
telas, conforme o extractor).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar


class BaseExtractor(ABC):
    """Contrato comum dos extractors de código-fonte legado."""

    #: Identificador curto do extractor (ex.: "sql", "isam", "screens").
    name: ClassVar[str] = ""

    @staticmethod
    @abstractmethod
    def extract(content: str, source_file: str = "") -> list:
        """Extrai definições do conteúdo de um arquivo-fonte."""
        raise NotImplementedError


def entity_extractors() -> tuple[type[BaseExtractor], ...]:
    """Extractors de entidades, na ordem oficial de aplicação do parser."""
    from .sql_extractor import SQLExtractor
    from .isam_extractor import ISAMExtractor
    from .dbf_extractor import DBFExtractor
    from .recital_extractor import RecitalExtractor

    return (SQLExtractor, ISAMExtractor, DBFExtractor, RecitalExtractor)
