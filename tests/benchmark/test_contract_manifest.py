"""§6 — Manifesto do experimento: create_contract → manifesto → round-trip.

Cobertura:

- ``create_contract`` aceita os campos do contrato como kwargs; preenche
  automaticamente ``schema_version`` ("1.0") e ``created_at`` (ISO8601 UTC);
- ``to_manifest_dict`` contém TODOS os campos do §6, incluindo
  ``think_time_profile`` como dict com ``type`` e ``sha256``;
- ``write_manifest`` grava ``experiment-manifest.json`` válido no diretório
  do experimento e retorna o caminho do arquivo;
- ``load_contract`` lê o manifesto e faz round-trip fiel (mesmo canonical);
- ``canonical_json`` é determinístico: duas chamadas produzem o mesmo texto,
  e o formato é exatamente ``json.dumps(manifesto, sort_keys=True,
  separators=(",", ":"))`` (interpretação fixada por este teste);
- ``sha256()`` é o SHA-256 hex do ``canonical_json``.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.contract import (  # noqa: E402
    StopConditions,
    ThinkTimeProfile,
    create_contract,
    load_contract,
)

_KWARGS = {
    "experiment_id": "exp-p1-manifest",
    "journey_set_sha256": "a" * 64,
    "dataset_sha256": "b" * 64,
    "application_version_sha256": "c" * 64,
    "seed": 42,
    "terminal_geometry": "80x24",
    "concurrency_levels": (1, 5, 10),
    "warmup_seconds": 30,
    "measurement_seconds": 60,
    "cooldown_seconds": 15,
    "iterations": 3,
    "think_time_profile": ThinkTimeProfile(
        type="deterministic", sha256="d" * 64, params={"fixed_ms": 100}),
    "stop_conditions": StopConditions(),
    "environments": ("aix", "linux"),
}

# Todos os campos exigidos pelo §6 no manifesto.
_CAMPOS_MANIFESTO = {
    "schema_version",
    "experiment_id",
    "created_at",
    "journey_set_sha256",
    "dataset_sha256",
    "application_version_sha256",
    "seed",
    "terminal_geometry",
    "concurrency_levels",
    "warmup_seconds",
    "measurement_seconds",
    "cooldown_seconds",
    "iterations",
    "think_time_profile",
    "stop_conditions",
    "environments",
}


class TestContractManifest(unittest.TestCase):
    """Manifesto completo, gravável, recarregável e canônico."""

    def setUp(self) -> None:
        self.contrato = create_contract(**_KWARGS)

    def test_manifesto_contem_todos_os_campos(self) -> None:
        manifesto = self.contrato.to_manifest_dict()
        faltantes = _CAMPOS_MANIFESTO - set(manifesto)
        self.assertFalse(faltantes, f"campos ausentes no manifesto: {faltantes}")
        self.assertEqual("1.0", manifesto["schema_version"])
        self.assertEqual("exp-p1-manifest", manifesto["experiment_id"])
        self.assertTrue(manifesto["created_at"], "created_at vazio")
        self.assertEqual(42, manifesto["seed"])
        self.assertEqual("80x24", manifesto["terminal_geometry"])
        self.assertEqual([1, 5, 10], list(manifesto["concurrency_levels"]))
        self.assertEqual(30, manifesto["warmup_seconds"])
        self.assertEqual(60, manifesto["measurement_seconds"])
        self.assertEqual(15, manifesto["cooldown_seconds"])
        self.assertEqual(3, manifesto["iterations"])
        self.assertEqual(["aix", "linux"], list(manifesto["environments"]))
        # think_time_profile serializado com type + sha256
        perfil = manifesto["think_time_profile"]
        self.assertEqual("deterministic", perfil["type"])
        self.assertEqual("d" * 64, perfil["sha256"])

    def test_write_manifest_grava_json_valido(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            caminho = self.contrato.write_manifest(Path(tmp))
            self.assertEqual("experiment-manifest.json", caminho.name)
            self.assertTrue(caminho.is_file())
            conteudo = json.loads(caminho.read_text(encoding="utf-8"))
        self.assertEqual(self.contrato.to_manifest_dict(), conteudo)

    def test_load_contract_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            caminho = self.contrato.write_manifest(Path(tmp))
            recarregado = load_contract(caminho)
        self.assertEqual(self.contrato.canonical_json(),
                         recarregado.canonical_json())
        self.assertEqual(self.contrato.sha256(), recarregado.sha256())
        # tipos do contrato preservados no round-trip
        self.assertEqual((1, 5, 10), tuple(recarregado.concurrency_levels))
        self.assertEqual(("aix", "linux"), tuple(recarregado.environments))

    def test_canonical_json_deterministico(self) -> None:
        primeiro = self.contrato.canonical_json()
        segundo = self.contrato.canonical_json()
        self.assertEqual(primeiro, segundo)
        # formato canônico: sort_keys + separadores compactos
        esperado = json.dumps(self.contrato.to_manifest_dict(),
                              sort_keys=True, separators=(",", ":"))
        self.assertEqual(esperado, primeiro)
        # sha256 do contrato = sha256 do canonical_json
        self.assertEqual(
            hashlib.sha256(primeiro.encode("utf-8")).hexdigest(),
            self.contrato.sha256(),
        )


if __name__ == "__main__":
    unittest.main()
