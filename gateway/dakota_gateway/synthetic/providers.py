from __future__ import annotations

import random
import re
import string
import uuid as _uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Provider base
# ---------------------------------------------------------------------------


class DataProvider(ABC):
    """Provider generico de dados sinteticos."""

    name: str = "base"

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    @abstractmethod
    def generate(self, **kwargs) -> Any:
        ...

    def reseed(self, seed: int) -> None:
        self._rng = random.Random(seed)


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


class PersonNameProvider(DataProvider):
    name = "person_name"

    FIRST_NAMES = [
        "Ana", "Beatriz", "Carla", "Daniela", "Eduarda", "Fernanda", "Gabriela", "Helena",
        "Igor", "Joao", "Kleber", "Lucas", "Marcos", "Natalia", "Otavio", "Paulo",
        "Rafael", "Silvia", "Tatiana", "Ubiratan", "Valeria", "Wagner", "Xenia",
        "Amanda", "Bruno", "Camila", "Diego", "Elaine", "Felipe", "Gustavo",
        "Henrique", "Isabela", "Juliana", "Karina", "Leonardo", "Mariana",
        "Nicolas", "Olivia", "Patricia", "Renata", "Sergio", "Thiago",
    ]
    LAST_NAMES = [
        "Silva", "Santos", "Oliveira", "Souza", "Lima", "Pereira", "Costa",
        "Ferreira", "Rodrigues", "Almeida", "Nascimento", "Araujo", "Barbosa",
        "Cardoso", "Carvalho", "Castro", "Dias", "Duarte", "Freitas", "Gomes",
    ]

    def generate(self, **kwargs) -> str:
        first = self._rng.choice(self.FIRST_NAMES)
        last = self._rng.choice(self.LAST_NAMES)
        return f"{first} {last}"


class CompanyNameProvider(DataProvider):
    name = "company_name"

    PREFIXES = ["", "Comercio", "Industria", "Servicos", "Tecnologia", "Distribuidora", "Importadora"]
    BASES = [
        "Alvorada", "Bandeirantes", "Caravelas", "Dourados", "Estrela",
        "Fenix", "Gloria", "Horizonte", "Ipiranga", "Jequitiba",
        "Meridional", "NorteSul", "OuroVerde", "Planalto", "RioBranco",
    ]
    SUFFIXES = ["Ltda", "S.A.", "EIRELI", "ME", "EPP"]

    def generate(self, **kwargs) -> str:
        prefix = self._rng.choice(self.PREFIXES)
        base = self._rng.choice(self.BASES)
        suffix = self._rng.choice(self.SUFFIXES)
        if prefix:
            return f"{prefix} {base} {suffix}"
        return f"{base} {suffix}"


class CPFProvider(DataProvider):
    name = "cpf"

    def generate(self, **kwargs) -> str:
        n = [self._rng.randint(0, 9) for _ in range(9)]
        n.append(self._calc_dv(n, 10))
        n.append(self._calc_dv(n, 11))
        raw = "".join(str(d) for d in n)
        return f"{raw[:3]}.{raw[3:6]}.{raw[6:9]}-{raw[9:]}"

    @staticmethod
    def _calc_dv(digits: list[int], factor: int) -> int:
        total = sum(d * (factor - i) for i, d in enumerate(digits))
        remainder = total % 11
        return 0 if remainder < 2 else 11 - remainder


class CNPJProvider(DataProvider):
    name = "cnpj"

    def generate(self, **kwargs) -> str:
        n = [self._rng.randint(0, 9) for _ in range(8)] + [0, 0, 0, 1]
        n.append(self._calc_dv(n, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]))
        n.append(self._calc_dv(n, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]))
        raw = "".join(str(d) for d in n)
        return f"{raw[:2]}.{raw[2:5]}.{raw[5:8]}/{raw[8:12]}-{raw[12:]}"

    @staticmethod
    def _calc_dv(digits: list[int], weights: list[int]) -> int:
        total = sum(d * w for d, w in zip(digits, weights))
        remainder = total % 11
        return 0 if remainder < 2 else 11 - remainder


class RGProvider(DataProvider):
    name = "rg"

    def generate(self, **kwargs) -> str:
        raw = "".join(str(self._rng.randint(0, 9)) for _ in range(9))
        return f"{raw[:2]}.{raw[2:5]}.{raw[5:8]}-{raw[8]}"


class PhoneProvider(DataProvider):
    name = "phone"

    DDD = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35, 37, 38,
           41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64, 65, 66, 67, 68,
           69, 71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92, 93, 94, 95,
           96, 97, 98, 99]

    def generate(self, **kwargs) -> str:
        ddd = self._rng.choice(self.DDD)
        prefix = 90000 + self._rng.randint(0, 9999)
        suffix = self._rng.randint(0, 9999)
        return f"({ddd}) {prefix}-{suffix:04d}"


class EmailProvider(DataProvider):
    name = "email"

    DOMAINS = ["email.com.br", "provedor.com.br", "corp.com.br", "empresa.com.br", "mail.com"]

    def generate(self, **kwargs) -> str:
        name_part = kwargs.get("name", "").lower().replace(" ", ".") if "name" in kwargs else ""
        if not name_part:
            letters = string.ascii_lowercase
            name_part = "".join(self._rng.choice(letters) for _ in range(self._rng.randint(5, 12)))
        domain = self._rng.choice(self.DOMAINS)
        return f"{name_part}@{domain}"


class AddressProvider(DataProvider):
    name = "address"

    STREETS = [
        "Rua das Flores", "Avenida Paulista", "Rua Augusta", "Alameda Santos",
        "Rua da Consolacao", "Avenida Brigadeiro Faria Lima", "Rua Oscar Freire",
        "Avenida Reboucas", "Rua Teodoro Sampaio", "Rua Cardeal Arcoverde",
    ]
    NEIGHBORHOODS = [
        "Centro", "Jardins", "Vila Mariana", "Pinheiros", "Moema",
        "Itaim Bibi", "Perdizes", "Bela Vista", "Consolacao", "Santana",
    ]
    CITIES = [
        "Sao Paulo", "Rio de Janeiro", "Belo Horizonte", "Curitiba", "Porto Alegre",
        "Salvador", "Fortaleza", "Recife", "Brasilia", "Campinas",
    ]
    STATES = ["SP", "RJ", "MG", "PR", "RS", "BA", "CE", "PE", "DF", "SC"]

    def generate(self, **kwargs) -> str:
        street = self._rng.choice(self.STREETS)
        number = self._rng.randint(1, 9999)
        return f"{street}, {number}"


class CEPProvider(DataProvider):
    name = "cep"

    def generate(self, **kwargs) -> str:
        raw = "".join(str(self._rng.randint(0, 9)) for _ in range(8))
        return f"{raw[:5]}-{raw[5:]}"


class DateProvider(DataProvider):
    name = "date"

    def generate(self, **kwargs) -> str:
        start = kwargs.get("start_date", datetime(2000, 1, 1))
        end = kwargs.get("end_date", datetime(2030, 12, 31))
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if isinstance(end, str):
            end = datetime.fromisoformat(end)
        delta = (end - start).days
        random_days = self._rng.randint(0, delta) if delta > 0 else 0
        return (start + timedelta(days=random_days)).strftime("%Y-%m-%d")


class DatetimeProvider(DataProvider):
    name = "datetime"

    def generate(self, **kwargs) -> str:
        start = kwargs.get("start_date", datetime(2000, 1, 1))
        end = kwargs.get("end_date", datetime(2030, 12, 31))
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if isinstance(end, str):
            end = datetime.fromisoformat(end)
        delta_seconds = (end - start).total_seconds()
        random_seconds = self._rng.uniform(0, delta_seconds) if delta_seconds > 0 else 0
        return (start + timedelta(seconds=random_seconds)).strftime("%Y-%m-%d %H:%M:%S")


class NumberProvider(DataProvider):
    name = "number"

    def generate(self, **kwargs) -> int:
        min_v = kwargs.get("min", 0)
        max_v = kwargs.get("max", 999999)
        return self._rng.randint(min_v, max_v)


class DecimalProvider(DataProvider):
    name = "decimal"

    def generate(self, **kwargs) -> float:
        min_v = kwargs.get("min", 0.0)
        max_v = kwargs.get("max", 999999.99)
        precision = kwargs.get("precision", 2)
        value = self._rng.uniform(min_v, max_v)
        return round(value, precision)


class MoneyProvider(DataProvider):
    name = "money"

    def generate(self, **kwargs) -> str:
        min_v = kwargs.get("min", 0.01)
        max_v = kwargs.get("max", 999999.99)
        value = self._rng.uniform(min_v, max_v)
        return f"{value:,.2f}"


class ChoiceProvider(DataProvider):
    name = "choice"

    def generate(self, **kwargs) -> str:
        choices = kwargs.get("choices", ["A", "B", "C"])
        if isinstance(choices, str):
            choices = [c.strip() for c in choices.split(",")]
        return self._rng.choice(choices)


class BooleanProvider(DataProvider):
    name = "boolean"

    def generate(self, **kwargs) -> bool:
        return self._rng.choice([True, False])


class UUIDProvider(DataProvider):
    name = "uuid"

    def generate(self, **kwargs) -> str:
        return str(_uuid.UUID(int=self._rng.getrandbits(128)))


class SequenceProvider(DataProvider):
    name = "sequence"

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self._counter = 0

    def generate(self, **kwargs) -> int:
        start = kwargs.get("start", 1)
        step = kwargs.get("step", 1)
        value = start + self._counter * step
        self._counter += 1
        return value

    def reseed(self, seed: int) -> None:
        super().reseed(seed)
        self._counter = 0


class TextProvider(DataProvider):
    name = "text"

    WORDS = [
        "lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing", "elit",
        "sed", "do", "eiusmod", "tempor", "incididunt", "ut", "labore", "et", "dolore",
        "magna", "aliqua", "enim", "ad", "minim", "veniam", "quis", "nostrud",
    ]

    def generate(self, **kwargs) -> str:
        min_len = kwargs.get("min_length", 5)
        max_len = kwargs.get("max_length", 200)
        words = []
        total_len = 0
        while total_len < min_len:
            w = self._rng.choice(self.WORDS)
            words.append(w)
            total_len += len(w) + 1
        while total_len < max_len and self._rng.random() > 0.3:
            w = self._rng.choice(self.WORDS)
            words.append(w)
            total_len += len(w) + 1
        result = " ".join(words)
        if len(result) > max_len:
            result = result[:max_len].rsplit(" ", 1)[0]
        return result


class CodeProvider(DataProvider):
    name = "code"

    def generate(self, **kwargs) -> str:
        prefix = kwargs.get("prefix", "")
        min_v = kwargs.get("min", 1)
        max_v = kwargs.get("max", 999999)
        width = kwargs.get("width", 6)
        value = self._rng.randint(min_v, max_v)
        return f"{prefix}{value:0{width}d}"


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


class ProviderRegistry:
    """Registro central de providers por nome."""

    def __init__(self):
        self._providers: dict[str, DataProvider] = {}

    def register(self, provider: DataProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> Optional[DataProvider]:
        return self._providers.get(name)

    def names(self) -> list[str]:
        return list(self._providers.keys())


# Instancias default
person_name_provider = PersonNameProvider()
company_name_provider = CompanyNameProvider()
cpf_provider = CPFProvider()
cnpj_provider = CNPJProvider()
rg_provider = RGProvider()
phone_provider = PhoneProvider()
email_provider = EmailProvider()
address_provider = AddressProvider()
cep_provider = CEPProvider()
date_provider = DateProvider()
datetime_provider = DatetimeProvider()
number_provider = NumberProvider()
decimal_provider = DecimalProvider()
money_provider = MoneyProvider()
choice_provider = ChoiceProvider()
boolean_provider = BooleanProvider()
uuid_provider = UUIDProvider()
sequence_provider = SequenceProvider()
text_provider = TextProvider()
code_provider = CodeProvider()


def default_registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    for p in [
        person_name_provider, company_name_provider, cpf_provider, cnpj_provider,
        rg_provider, phone_provider, email_provider, address_provider, cep_provider,
        date_provider, datetime_provider, number_provider, decimal_provider,
        money_provider, choice_provider, boolean_provider, uuid_provider,
        sequence_provider, text_provider, code_provider,
    ]:
        reg.register(p)
    return reg
