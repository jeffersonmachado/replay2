"""Item 4 (v0.9.11) — preparação do benchmark oficial reproduzível (v8).

Dois eixos:

1. Hashes de proveniência CALCULADOS dos artefatos (``benchmark.provenance``):
   o v7 foi marcado INCONCLUSIVE porque o operador digitou hashes
   placeholder/idênticos. O caminho oficial do v8 calcula o sha256 real a
   partir dos arquivos (arquivo único → sha256 do conteúdo; diretório →
   hash de conjunto no mesmo esquema do ``evidence-manifest.sha256``:
   linhas ``"<sha256>  <path relativo>\\n"`` ordenadas, sha256 da
   concatenação). O ``benchmark create`` aceita os flags ``--journey-set``,
   ``--dataset``, ``--application`` e ``--think-time-profile`` e grava
   ``provenance.json`` ao lado do manifesto.

2. INCONCLUSIVE por falta de evidência essencial: mesmo com proveniência
   válida (hashes calculados), a decisão não recomenda plataforma/capacidade
   quando falta cobertura de coletores (grupo rede), clock skew medido ou
   evidência de paridade funcional.

Estes testes DEVEM FALHAR antes da implementação e PASSAR depois dela.
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

from dakota_gateway.benchmark.comparison import (  # noqa: E402
    build_comparison,
    build_decision,
)
from dakota_gateway.benchmark.contract import (  # noqa: E402
    ContractViolation,
    StopConditions,
    ThinkTimeProfile,
    create_contract,
    load_contract,
    validate_provenance_hashes,
)
from dakota_gateway.benchmark.models import (  # noqa: E402
    EnvironmentRunResult,
    ExperimentResult,
    OperationSample,
)
from dakota_gateway.benchmark.provenance import (  # noqa: E402
    aplicar_hashes_proveniencia,
    calcular_hashes_proveniencia,
    escrever_proveniencia,
    sha256_arquivo,
    sha256_conjunto,
)

BASELINE = "aix-power"
TARGET = "linux-x86"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class TestHashCalculado(unittest.TestCase):
    """sha256_arquivo / sha256_conjunto — identidade real dos artefatos."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_sha256_arquivo_confere_com_conteudo(self) -> None:
        arq = self.tmp / "dataset.jsonl"
        arq.write_bytes(b'{"seed": 42}\n')
        self.assertEqual(_sha256(b'{"seed": 42}\n'), sha256_arquivo(arq))

    def test_sha256_conjunto_arquivo_unico_e_o_proprio_hash(self) -> None:
        arq = self.tmp / "journey.jsonl"
        arq.write_bytes(b"linha1\nlinha2\n")
        self.assertEqual(sha256_arquivo(arq), sha256_conjunto(arq))

    def test_sha256_conjunto_diretorio_esquema_evidence_manifest(self) -> None:
        """Hash de conjunto = sha256 das linhas '<sha256>  <relativo>\\n'."""
        raiz = self.tmp / "jornadas"
        (raiz / "sub").mkdir(parents=True)
        (raiz / "a.json").write_bytes(b"A")
        (raiz / "sub" / "b.json").write_bytes(b"B")
        esperado = _sha256(
            (f"{_sha256(b'A')}  a.json\n"
             f"{_sha256(b'B')}  sub/b.json\n").encode("utf-8"))
        self.assertEqual(esperado, sha256_conjunto(raiz))

    def test_sha256_conjunto_ordem_estavel_e_sensivel_a_conteudo(self) -> None:
        raiz = self.tmp / "conj"
        raiz.mkdir()
        # criação em ordem não-alfabética: a ordenação é do caminho relativo
        (raiz / "z.txt").write_bytes(b"1")
        (raiz / "a.txt").write_bytes(b"2")
        hash1 = sha256_conjunto(raiz)
        self.assertEqual(hash1, sha256_conjunto(raiz))
        (raiz / "a.txt").write_bytes(b"2!")
        self.assertNotEqual(hash1, sha256_conjunto(raiz))

    def test_caminho_inexistente_e_diretorio_vazio_sao_erro(self) -> None:
        with self.assertRaises(ContractViolation):
            sha256_conjunto(self.tmp / "nao-existe")
        vazio = self.tmp / "vazio"
        vazio.mkdir()
        with self.assertRaises(ContractViolation):
            sha256_conjunto(vazio)


class TestAplicacaoNoContrato(unittest.TestCase):
    """Injeção dos hashes calculados no dict do contrato (sem ambiguidade)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / "journey.jsonl").write_bytes(b"j\n")
        (self.tmp / "dataset.jsonl").write_bytes(b"d\n")
        app = self.tmp / "app"
        app.mkdir()
        (app / "est361.prg").write_bytes(b"fonte\n")
        (self.tmp / "think.json").write_text(
            json.dumps({"type": "deterministic", "deltas_ms": [100, 200]}),
            encoding="utf-8")

    def _calculados(self) -> dict:
        return calcular_hashes_proveniencia(
            journey_set=self.tmp / "journey.jsonl",
            dataset=self.tmp / "dataset.jsonl",
            application=self.tmp / "app",
            think_time_profile=self.tmp / "think.json")

    def test_calcula_os_quatro_campos_com_fonte(self) -> None:
        calc = self._calculados()
        self.assertEqual(
            {"journey_set_sha256", "dataset_sha256",
             "application_version_sha256", "think_time_profile.sha256"},
            set(calc))
        for reg in calc.values():
            self.assertEqual(64, len(reg["sha256"]))
            self.assertTrue(reg["fonte"])
            self.assertIn(reg["tipo"], ("arquivo", "conjunto"))
        self.assertEqual("conjunto",
                         calc["application_version_sha256"]["tipo"])

    def test_aplicar_preenche_campos_ausentes(self) -> None:
        dados = aplicar_hashes_proveniencia(
            {"experiment_id": "exp"}, self._calculados())
        self.assertEqual(64, len(dados["journey_set_sha256"]))
        self.assertEqual(64, len(dados["dataset_sha256"]))
        self.assertEqual(64, len(dados["application_version_sha256"]))
        self.assertEqual(
            64, len(dados["think_time_profile"]["sha256"]))

    def test_aplicar_mesmo_hash_aceito_e_conflito_recusado(self) -> None:
        calc = self._calculados()
        igual = calc["dataset_sha256"]["sha256"]
        dados = aplicar_hashes_proveniencia(
            {"dataset_sha256": igual}, calc)
        self.assertEqual(igual, dados["dataset_sha256"])
        with self.assertRaises(ContractViolation):
            aplicar_hashes_proveniencia(
                {"dataset_sha256": "f" * 64}, calc)

    def test_contrato_com_hashes_calculados_passa_na_validacao(self) -> None:
        dados = aplicar_hashes_proveniencia({
            "experiment_id": "exp-calc",
            "seed": 42, "terminal_geometry": "80x24",
            "concurrency_levels": [1, 5],
            "warmup_seconds": 30, "measurement_seconds": 120,
            "cooldown_seconds": 30, "iterations": 2,
            "think_time_profile": {"type": "deterministic", "params": {}},
            "stop_conditions": {},
            "environments": [BASELINE, TARGET],
        }, self._calculados())
        contrato = create_contract(**dados)
        self.assertEqual([], validate_provenance_hashes(
            contrato, exigir_presenca=True))

    def test_provenance_json_gravado_no_experimento(self) -> None:
        exp_dir = self.tmp / "exp"
        caminho = escrever_proveniencia(exp_dir, self._calculados())
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        self.assertEqual("provenance/v1", dados["schema"])
        self.assertEqual(4, len(dados["artefatos"]))
        self.assertTrue(all(a["sha256"] for a in dados["artefatos"]))


class TestCreateCliComCaminhos(unittest.TestCase):
    """``benchmark create`` com flags de caminho calcula os hashes reais."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / "journey.jsonl").write_bytes(b"j\n")
        (self.tmp / "dataset.jsonl").write_bytes(b"d\n")
        app = self.tmp / "app"
        app.mkdir()
        (app / "est361.prg").write_bytes(b"fonte\n")
        (self.tmp / "think.json").write_text(
            json.dumps({"type": "deterministic", "deltas_ms": [100]}),
            encoding="utf-8")
        self.contrato_json = self.tmp / "contract.json"
        self.artifacts = self.tmp / "artifacts"
        self.db = self.tmp / "replay.db"

    def _escrever_contrato(self, **over) -> None:
        dados = {
            "experiment_id": "exp-v8",
            "seed": 42, "terminal_geometry": "80x24",
            "concurrency_levels": [1, 5, 10, 20],
            "warmup_seconds": 30, "measurement_seconds": 120,
            "cooldown_seconds": 30, "iterations": 2,
            "think_time_profile": {"type": "deterministic", "params": {}},
            "stop_conditions": {},
            "environments": [BASELINE, TARGET],
        }
        dados.update(over)
        self.contrato_json.write_text(json.dumps(dados), encoding="utf-8")

    def _create(self, *extra: str) -> int:
        from dakota_gateway.cli import main
        return main([
            "benchmark", "create",
            "--contract", str(self.contrato_json),
            "--artifacts-dir", str(self.artifacts),
            "--db", str(self.db),
            *extra,
        ])

    def test_create_com_caminhos_calcula_hashes_e_grava_proveniencia(
            self) -> None:
        self._escrever_contrato()
        rc = self._create(
            "--journey-set", str(self.tmp / "journey.jsonl"),
            "--dataset", str(self.tmp / "dataset.jsonl"),
            "--application", str(self.tmp / "app"),
            "--think-time-profile", str(self.tmp / "think.json"))
        self.assertEqual(0, rc)
        exp_dir = self.artifacts / "exp-v8"
        contrato = load_contract(exp_dir / "experiment-manifest.json")
        calc = calcular_hashes_proveniencia(
            journey_set=self.tmp / "journey.jsonl",
            dataset=self.tmp / "dataset.jsonl",
            application=self.tmp / "app",
            think_time_profile=self.tmp / "think.json")
        self.assertEqual(calc["journey_set_sha256"]["sha256"],
                         contrato.journey_set_sha256)
        self.assertEqual(calc["dataset_sha256"]["sha256"],
                         contrato.dataset_sha256)
        self.assertEqual(calc["application_version_sha256"]["sha256"],
                         contrato.application_version_sha256)
        self.assertEqual(calc["think_time_profile.sha256"]["sha256"],
                         contrato.think_time_profile.sha256)
        self.assertTrue((exp_dir / "provenance.json").is_file())
        self.assertEqual([], validate_provenance_hashes(
            contrato, exigir_presenca=True))

    def test_create_conflito_string_x_caminho_falha(self) -> None:
        """String no JSON divergindo do hash calculado → recusa (rc != 0)."""
        self._escrever_contrato(dataset_sha256="f" * 64)
        rc = self._create("--dataset", str(self.tmp / "dataset.jsonl"))
        self.assertNotEqual(0, rc)
        self.assertFalse((self.artifacts / "exp-v8"
                          / "experiment-manifest.json").exists())

    def test_create_placeholder_recusado_mesmo_sem_caminhos(self) -> None:
        """Retrocompatível (strings no JSON), mas placeholder óbvio é erro."""
        self._escrever_contrato(
            journey_set_sha256="0" * 64,
            dataset_sha256="a" * 64,
            application_version_sha256="b" * 64,
            think_time_profile={"type": "none", "sha256": "c" * 64,
                                "params": {}})
        rc = self._create()
        self.assertNotEqual(0, rc)

    def test_create_sem_caminhos_continua_aceitando_strings_validas(
            self) -> None:
        self._escrever_contrato(
            journey_set_sha256="a" * 64,
            dataset_sha256="b" * 64,
            application_version_sha256="c" * 64,
            think_time_profile={"type": "none", "sha256": "d" * 64,
                                "params": {}})
        rc = self._create()
        self.assertEqual(0, rc)
        contrato = load_contract(self.artifacts / "exp-v8"
                                 / "experiment-manifest.json")
        self.assertEqual("a" * 64, contrato.journey_set_sha256)


# ── item 2: INCONCLUSIVE por falta de evidência essencial ────────────────────

def _contrato_v8() -> object:
    """Contrato estilo v8: hashes distintos de 64 hex (como os calculados)."""
    return create_contract(
        experiment_id="exp-v8-gates",
        journey_set_sha256="1" * 40 + "abcdef01" * 3,
        dataset_sha256="2" * 40 + "abcdef02" * 3,
        application_version_sha256="3" * 40 + "abcdef03" * 3,
        seed=42, terminal_geometry="80x24",
        concurrency_levels=(1, 5),
        warmup_seconds=30, measurement_seconds=120,
        cooldown_seconds=30, iterations=2,
        think_time_profile=ThinkTimeProfile(
            type="deterministic", sha256="4" * 40 + "abcdef04" * 3,
            params={}),
        stop_conditions=StopConditions(),
        environments=(BASELINE, TARGET))


def _amostra(env: str, idx: int, *, checada: bool = True) -> OperationSample:
    ns0 = 1_000_000_000 + idx * 20_000_000
    return OperationSample(
        experiment_id="exp-v8-gates", environment_id=env, iteration=1,
        concurrency=1, virtual_user_id="vu-1", journey_id="j",
        step_id=f"ev-{idx}", phase="MEASUREMENT", started_ns=ns0,
        finished_ns=ns0 + 10_000_000, latency_ms=10.0,
        success=True, timeout=False, functional_divergence=False,
        error_code=None, screen_sig_checked=checada,
        expected_screen_sig="sha256:x" if checada else None,
        observed_screen_sig="sha256:x" if checada else None)


def _host_completo(ts_ms: int) -> dict:
    """Amostra com TODOS os grupos essenciais (cpu/mem/pag/disco/rede/rq)."""
    return {"ts_ms": ts_ms, "cpu_pct": 12.0, "mem_pct": 30.0,
            "swap_pct": 0.5, "disk_latency_ms": 2.0, "iops": 40.0,
            "disk_busy_pct": 5.0, "load1": 0.4,
            "net_rx_kbs": 120.0, "net_tx_kbs": 90.0}


def _resultado(tmp: Path, *, host: dict | None = None,
               skew_medido: bool = True,
               checada: bool = True) -> ExperimentResult:
    """Resultado com todos os gates verdes, exceto o eixo sob teste."""
    host = _host_completo if host is None else host
    runs = []
    for env, nome in ((BASELINE, "host-base.jsonl"),
                      (TARGET, "host-alvo.jsonl")):
        (tmp / nome).write_text(
            "".join(json.dumps(host(1000 + i * 5000)) + "\n"
                    for i in range(4)), encoding="utf-8")
        run = EnvironmentRunResult(
            environment_id=env, iteration=1, concurrency=1,
            status="COMPLETED",
            samples=[_amostra(env, i, checada=checada) for i in range(20)],
            host_samples_path=str(tmp / nome))
        run.host_clock_offset_measured = skew_medido
        run.host_clock_offset_ms = 0 if skew_medido else None
        run.checkpoints_executed = 20
        run.checkpoints_checked = 20
        runs.append(run)
    return ExperimentResult(
        contract_sha256="c" * 64, status="COMPLETED", runs=runs)


class TestInconclusiveEvidenciaEssencial(unittest.TestCase):
    """Mesmo com proveniência calculada, evidência essencial ausente barra
    qualquer recomendação (checklist do runbook v8)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _decisao(self, resultado: ExperimentResult):
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET,
            contract=_contrato_v8())
        return comparison, build_decision(resultado, comparison)

    def test_controle_tudo_presente_pass(self) -> None:
        _, decision = self._decisao(_resultado(self.tmp))
        self.assertEqual("PASS", decision.verdict)
        self.assertIsNotNone(decision.recommendation)

    def test_grupo_rede_ausente_inconclusive_sem_recomendacao(self) -> None:
        def host_sem_rede(ts_ms: int) -> dict:
            amostra = _host_completo(ts_ms)
            amostra.pop("net_rx_kbs")
            amostra.pop("net_tx_kbs")
            return amostra
        comparison, decision = self._decisao(
            _resultado(self.tmp, host=host_sem_rede))
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        for env in (BASELINE, TARGET):
            self.assertIn(
                "rede",
                comparison["collector_coverage"][env]["host"]
                ["grupos_ausentes"])

    def test_clock_skew_nao_medido_inconclusive_sem_recomendacao(self) -> None:
        _, decision = self._decisao(
            _resultado(self.tmp, skew_medido=False))
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        self.assertIn("clock skew", " ".join(decision.reasons))

    def test_sem_evidencia_funcional_inconclusive_sem_recomendacao(
            self) -> None:
        """Paridade funcional não verificada (nenhuma assinatura checada)."""
        _, decision = self._decisao(_resultado(self.tmp, checada=False))
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        self.assertIn("funcional", " ".join(decision.reasons))

    def test_proveniencia_placeholder_inconclusive_mesmo_com_o_resto_verde(
            self) -> None:
        """Contrato legado (hash ausente) carrega p/ auditoria, mas a
        decisão barra — INCONCLUSIVE sem recomendação."""
        manifesto = self.tmp / "manifest-legado.json"
        manifesto.write_text(json.dumps({
            "schema_version": "1.0",
            "experiment_id": "exp-legado",
            "created_at": "2026-09-01T00:00:00Z",
            "journey_set_sha256": "1" * 40 + "abcdef01" * 3,
            "dataset_sha256": "2" * 40 + "abcdef02" * 3,
            "application_version_sha256": "",
            "seed": 42, "terminal_geometry": "80x24",
            "concurrency_levels": [1], "warmup_seconds": 30,
            "measurement_seconds": 120, "cooldown_seconds": 30,
            "iterations": 1,
            "think_time_profile": {"type": "deterministic",
                                   "sha256": "4" * 40 + "abcdef04" * 3,
                                   "params": {}},
            "stop_conditions": {},
            "environments": [BASELINE, TARGET],
        }), encoding="utf-8")
        contrato = load_contract(manifesto)
        resultado = _resultado(self.tmp)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET,
            contract=contrato)
        decision = build_decision(resultado, comparison)
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        self.assertIn("proveni", " ".join(decision.reasons))


if __name__ == "__main__":
    unittest.main()
