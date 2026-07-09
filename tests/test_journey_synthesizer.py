#!/usr/bin/env python3
"""Testes para JourneySynthesizer — Capture-to-Synthetic Journey."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.entity_catalog import EntityDefinition, FieldDefinition
from dakota_gateway.source_analyzer.screen_entity_linker import ScreenEntityBinding


class JourneySynthesizerTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.d = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_capture_jsonl(self, inputs: list[str], screen_title: str = "Cadastro de Clientes") -> Path:
        """Cria arquivo .jsonl de captura simulado."""
        events = [
            {"type": "checkpoint", "session_id": "sess-001",
             "screen_sig": "SIG_0", "screen_sample": screen_title,
             "seq_global": 1, "norm_len": 400},
        ]
        for inp in inputs:
            events.append({"type": "bytes", "key_text": inp})
        p = self.d / "capture.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))
        return p

    def _make_source_dir(self) -> Path:
        """Cria diretorio fonte com .prg de exemplo."""
        sd = self.d / "source"
        sd.mkdir()
        (sd / "cadcli.prg").write_text("""
TITLE "Cadastro de Clientes"
@ 01,01 SAY "CPF"
@ 01,20 GET cCpf
@ 02,01 SAY "Nome"
@ 02,20 GET cNome
USE CLIENTES
APPEND BLANK
""")
        (sd / "schema.sql").write_text("""
CREATE TABLE CLIENTES (
    ID INTEGER PRIMARY KEY,
    CPF VARCHAR(14) NOT NULL,
    NOME VARCHAR(100) NOT NULL,
    EMAIL VARCHAR(100),
    TELEFONE VARCHAR(20)
);
""")
        return sd

    # ── 3.1: Captura simples vira template ──

    def test_capture_becomes_template(self):
        """CPF e nome viram placeholders, F10 permanece comando."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        capture_path = self._make_capture_jsonl(["123.456.789-09", "JOAO TESTE", "{KEY:F10}"])
        source_dir = self._make_source_dir()
        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, source_dir, name="teste_cadastro")

        self.assertIsNotNone(template)
        self.assertEqual(template.name, "teste_cadastro")
        self.assertIn("CLIENTES", template.entities_involved)

        # Verifica steps
        self.assertGreaterEqual(len(template.steps), 1)
        step = template.steps[0]

        # Deve ter 3 inputs: CPF, nome, F10
        self.assertEqual(len(step.inputs), 3)

        # F10 deve ser comando
        cmd = [i for i in step.inputs if i.method == "command"]
        self.assertEqual(len(cmd), 1)
        self.assertEqual(cmd[0].original, "{KEY:F10}")
        self.assertEqual(cmd[0].confidence, 1.0)

        # CPF deve ter placeholder
        cpf_inputs = [i for i in step.inputs if i.field_name and "cpf" in i.field_name.lower()]
        self.assertGreaterEqual(len(cpf_inputs), 1, "CPF deveria ser mapeado")
        self.assertTrue(cpf_inputs[0].placeholder, "CPF deve ter placeholder")

        # Nome deve ter placeholder
        nome_inputs = [i for i in step.inputs if i.field_name and "nome" in i.field_name.lower()]
        self.assertGreaterEqual(len(nome_inputs), 1, "NOME deveria ser mapeado")

        # Template deve ter evidence
        self.assertGreaterEqual(len(template.evidence), 1)
        self.assertIsNotNone(template.journey_id)

    # ── 3.2: Geracao de multiplas sessoes sinteticas ──

    def test_generate_multiple_synthetic_sessions(self):
        """Gera 10 sessoes com dados sinteticos diferentes."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        capture_path = self._make_capture_jsonl(["123.456.789-09", "JOAO TESTE"])
        source_dir = self._make_source_dir()
        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, source_dir, name="teste")
        result = syn.synthesize(template, samples=10, out_dir=out_dir, seed=42)

        self.assertEqual(result.samples, 10)
        self.assertEqual(result.generated_sessions, 10)
        self.assertEqual(len(result.warnings), 0)

        # Verifica arquivos de saida
        self.assertTrue((out_dir / "template.json").exists())
        self.assertTrue((out_dir / "dataset.jsonl").exists())
        self.assertTrue((out_dir / "sessions").is_dir())
        self.assertTrue((out_dir / "report.json").exists())

        # Verifica sessoes
        sessions_dir = out_dir / "sessions"
        session_files = sorted(sessions_dir.glob("session_*.jsonl"))
        self.assertEqual(len(session_files), 10)

        # Cada sessao deve ser JSONL valido
        for sf in session_files:
            content = sf.read_text().strip()
            self.assertTrue(content, f"Sessao vazia: {sf.name}")
            for line in content.split("\n"):
                if line.strip():
                    obj = json.loads(line)
                    self.assertIn("seq", obj)
                    self.assertIn("type", obj)
                    self.assertIn("value", obj)

        # Nenhum placeholder deve sobrar nos values
        for sf in session_files:
            for line in sf.read_text().split("\n"):
                if line.strip():
                    obj = json.loads(line)
                    val = obj.get("value", "")
                    self.assertNotIn("{{", val,
                                     f"Placeholder nao resolvido no value em {sf.name}: {val}")

        # CPFs devem ser diferentes entre sessoes
        first_cpf = None
        for sf in session_files[:3]:
            for line in sf.read_text().split("\n"):
                if line.strip() and '"type":"input"' in line:
                    obj = json.loads(line)
                    if obj.get("field", "").lower() == "cpf":
                        if first_cpf is None:
                            first_cpf = obj["value"]
                        # Nao precisa ser diferente deterministicamente com seed,
                        # mas deve ser valido (apenas digitos)
                        self.assertTrue(obj["value"].replace(".", "").replace("-", "").isdigit(),
                                        f"CPF invalido: {obj['value']}")

    # ── 3.3: Jornada multi-tela/multi-entidade ──

    def test_multi_screen_multi_entity(self):
        """Jornada com duas telas e duas entidades."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        # Cria fonte com duas entidades
        sd = self.d / "source"
        sd.mkdir()
        (sd / "cadcli.prg").write_text("""
TITLE "Cadastro de Clientes"
@ 01,01 SAY "CPF"
@ 01,20 GET cCpf
@ 02,01 SAY "Nome"
@ 02,20 GET cNome
USE CLIENTES
APPEND BLANK
""")
        (sd / "cadprod.prg").write_text("""
TITLE "Cadastro de Produtos"
@ 01,01 SAY "Descricao"
@ 01,20 GET cDesc
@ 02,01 SAY "Preco"
@ 02,20 GET nPreco
USE PRODUTOS
APPEND BLANK
""")
        (sd / "schema.sql").write_text("""
CREATE TABLE CLIENTES (
    ID INTEGER PRIMARY KEY,
    CPF VARCHAR(14) NOT NULL,
    NOME VARCHAR(100) NOT NULL
);
CREATE TABLE PRODUTOS (
    ID INTEGER PRIMARY KEY,
    DESCRICAO VARCHAR(200) NOT NULL,
    PRECO DECIMAL(10,2) NOT NULL
);
""")

        # Cria captura multi-tela
        events = [
            {"type": "checkpoint", "session_id": "sess-001",
             "screen_sig": "SIG_0", "screen_sample": "Cadastro de Clientes",
             "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "123.456.789-09"},
            {"type": "bytes", "key_text": "JOAO CLIENTE"},
            {"type": "checkpoint", "session_id": "sess-001",
             "screen_sig": "SIG_1", "screen_sample": "Cadastro de Produtos",
             "seq_global": 2, "norm_len": 410},
            {"type": "bytes", "key_text": "PRODUTO TESTE"},
            {"type": "bytes", "key_text": "99.90"},
        ]
        capture_path = self.d / "capture.jsonl"
        capture_path.write_text("\n".join(json.dumps(e) for e in events))

        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, sd, name="jornada_dupla")
        result = syn.synthesize(template, samples=5, out_dir=out_dir, seed=42)

        # Duas entidades envolvidas
        self.assertIn("CLIENTES", template.entities_involved)
        self.assertIn("PRODUTOS", template.entities_involved)
        self.assertEqual(len(template.entities_involved), 2)

        self.assertEqual(result.generated_sessions, 5)

        # Verifica que sessoes tem inputs de ambas as entidades
        sessions_dir = out_dir / "sessions"
        first_session = sorted(sessions_dir.glob("session_*.jsonl"))[0]
        content = first_session.read_text()
        lines = [json.loads(l) for l in content.strip().split("\n") if l.strip()]
        entities_found = set(obj.get("entity", "") for obj in lines if obj.get("entity"))
        self.assertIn("CLIENTES", entities_found)
        self.assertIn("PRODUTOS", entities_found)

    # ── 3.4: Campos tecnicos nao recebem texto livre ──

    def test_tech_fields_not_mapped_to_text(self):
        """ID nao deve receber texto livre."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        sd = self.d / "source"
        sd.mkdir()
        (sd / "cadcli.prg").write_text("""
TITLE "Cadastro de Clientes"
@ 01,01 SAY "Nome"
@ 01,20 GET cNome
@ 02,01 SAY "Endereco"
@ 02,20 GET cEnd
USE CLIENTES
APPEND BLANK
""")
        (sd / "schema.sql").write_text("""
CREATE TABLE CLIENTES (
    ID INTEGER PRIMARY KEY,
    NOME VARCHAR(100) NOT NULL,
    ENDERECO VARCHAR(200)
);
""")

        capture_path = self._make_capture_jsonl(["JOAO CLIENTE", "RUA TESTE"])
        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, sd, name="teste_tech")

        # Nenhum input de texto deve mapear para ID
        for step in template.steps:
            for inp in step.inputs:
                if inp.original_type in ("text", "text_long"):
                    self.assertNotEqual(
                        inp.field_name.upper() if inp.field_name else "",
                        "ID",
                        f"Texto '{inp.original}' nao deve mapear para ID"
                    )

    # ── 3.5: Relatorio ──

    def test_report_structure(self):
        """report.json deve conter todos os campos obrigatorios."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        capture_path = self._make_capture_jsonl(["123.456.789-09", "JOAO TESTE", "{KEY:F10}"])
        source_dir = self._make_source_dir()
        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, source_dir, name="teste_report")
        result = syn.synthesize(template, samples=2, out_dir=out_dir, seed=42)

        report_path = out_dir / "report.json"
        self.assertTrue(report_path.exists())

        report = json.loads(report_path.read_text())
        self.assertIn("journey_id", report)
        self.assertIn("name", report)
        self.assertIn("capture_source", report)
        self.assertIn("samples", report)
        self.assertIn("entities_involved", report)
        self.assertIn("total_sessions", report)
        self.assertIn("generated_sessions", report)
        self.assertIn("template_file", report)
        self.assertIn("dataset_file", report)
        self.assertIn("sessions_dir", report)
        self.assertIn("mapped_inputs", report)
        self.assertIn("command_inputs", report)
        self.assertIn("unmapped_inputs", report)
        self.assertIn("screen_mappings", report)
        self.assertIn("warnings", report)
        self.assertIn("evidence", report)

        self.assertEqual(report["samples"], 2)
        self.assertEqual(report["generated_sessions"], 2)
        self.assertIn("CLIENTES", report["entities_involved"])

    # ── Save/Load template ──

    def test_save_and_load_template(self):
        """Template salvo e recarregado mantem dados."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        capture_path = self._make_capture_jsonl(["123.456.789-09", "JOAO TESTE"])
        source_dir = self._make_source_dir()
        tmpl_path = self.d / "saved_template.json"

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, source_dir, name="salvo")
        syn.save_template(template, tmpl_path)

        self.assertTrue(tmpl_path.exists())

        loaded = syn.load_template(tmpl_path)
        self.assertEqual(loaded.name, "salvo")
        self.assertEqual(loaded.journey_id, template.journey_id)
        self.assertEqual(len(loaded.steps), len(template.steps))
        self.assertEqual(loaded.entities_involved, template.entities_involved)

    # ═══════════════════════════════════════════════════════════════
    # v0.2.2 — Regression tests: money, seed, report, screen_title
    # ═══════════════════════════════════════════════════════════════

    # ── 1: Campos monetários não geram texto ──

    def test_money_fields_do_not_generate_text(self):
        """VALOR/PRECO/TOTAL devem gerar decimal, nao lorem/texto."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        sd = self.d / "source"
        sd.mkdir()
        (sd / "cadprod.prg").write_text("""
TITLE "Cadastro de Produtos"
@ 01,01 SAY "Descricao"
@ 01,20 GET cDesc
@ 02,01 SAY "Valor"
@ 02,20 GET nValor
USE PRODUTOS
APPEND BLANK
""")
        (sd / "schema.sql").write_text("""
CREATE TABLE PRODUTOS (
    ID INTEGER PRIMARY KEY,
    DESCRICAO VARCHAR(200) NOT NULL,
    VALOR DECIMAL(10,2) NOT NULL
);
""")

        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Cadastro de Produtos", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "PRODUTO TESTE"},
            {"type": "bytes", "key_text": "10,50"},
        ]
        capture_path = self.d / "capture.jsonl"
        capture_path.write_text("\n".join(json.dumps(e) for e in events))

        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, sd, name="teste_valor")
        result = syn.synthesize(template, samples=5, out_dir=out_dir, seed=42)

        # Varre sessoes procurando VALOR
        value_fields = []
        sessions_dir = out_dir / "sessions"
        for sf in sorted(sessions_dir.glob("session_*.jsonl")):
            for line in sf.read_text().split("\n"):
                if line.strip():
                    obj = json.loads(line)
                    fn = (obj.get("field") or "").upper()
                    if fn in ("VALOR", "PRECO", "TOTAL"):
                        value_fields.append(obj["value"])

        self.assertGreater(len(value_fields), 0, "Nenhum campo VALOR encontrado")
        for v in value_fields:
            vstr = str(v)
            # Nao pode conter palavras lorem
            for bad in ("lorem", "ipsum", "consectetur", "labore", "nostrud",
                        "tempor", "magna", "elit", "dolor", "amet"):
                self.assertNotIn(bad, vstr.lower(),
                                 f"VALOR contem texto: '{vstr}'")
            # Deve bater regex numerica
            self.assertTrue(
                bool(__import__('re').match(r'^\d+([,.]\d{1,2})?$', vstr)),
                f"VALOR nao eh numerico: '{vstr}'"
            )

    # ── 2: Seed deterministico ──

    def test_synthesis_is_deterministic_with_same_seed(self):
        """Mesmo seed gera arquivos identicos entre execucoes."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        capture_path = self._make_capture_jsonl(["123.456.789-09", "JOAO TESTE"])
        source_dir = self._make_source_dir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, source_dir, name="det_test")

        out1 = self.d / "out1"
        out2 = self.d / "out2"
        out1.mkdir()
        out2.mkdir()

        syn.synthesize(template, samples=5, out_dir=out1, seed=42)
        syn.synthesize(template, samples=5, out_dir=out2, seed=42)

        # Compara datasets
        ds1 = (out1 / "dataset.jsonl").read_text()
        ds2 = (out2 / "dataset.jsonl").read_text()
        self.assertEqual(ds1, ds2, "Datasets diferem com mesmo seed")

        # Compara sessoes
        for i in range(1, 6):
            s1 = (out1 / "sessions" / f"session_{i:06d}.jsonl").read_text()
            s2 = (out2 / "sessions" / f"session_{i:06d}.jsonl").read_text()
            self.assertEqual(s1, s2, f"Session {i} difere com mesmo seed")

    # ── 3: Report com inputs detalhados ──

    def test_report_contains_detailed_inputs(self):
        """report.json deve conter screen_mappings[].inputs[] detalhados."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        capture_path = self._make_capture_jsonl(["123.456.789-09", "JOAO TESTE", "{KEY:F10}"])
        source_dir = self._make_source_dir()
        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, source_dir, name="report_test")
        syn.synthesize(template, samples=2, out_dir=out_dir, seed=42)

        report = json.loads((out_dir / "report.json").read_text())

        self.assertIn("screen_mappings", report)
        self.assertGreater(len(report["screen_mappings"]), 0)

        sm = report["screen_mappings"][0]
        self.assertIn("inputs", sm, "screen_mapping deve ter inputs detalhados")
        self.assertGreater(len(sm["inputs"]), 0)

        # Cada input deve ter os campos obrigatorios
        for inp in sm["inputs"]:
            self.assertIn("original", inp)
            self.assertIn("placeholder", inp)
            self.assertIn("field_name", inp)
            self.assertIn("method", inp)
            self.assertIn("confidence", inp)
            self.assertIn("evidence", inp)

        # Comando deve estar presente
        commands = [i for i in sm["inputs"] if i.get("method") == "command"]
        self.assertGreater(len(commands), 0, "Comando nao encontrado no report")
        cmd = commands[0]
        self.assertIsNone(cmd["placeholder"])
        self.assertIsNone(cmd["field_name"])
        self.assertEqual(cmd["confidence"], 1.0)

    # ── 4: screen_title preservado ──

    def test_screen_title_is_preserved(self):
        """screen_title deve aparecer no template e report quando disponivel."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        sd = self.d / "source"
        sd.mkdir()
        (sd / "cadcli.prg").write_text("""
TITLE "Cadastro de Clientes"
@ 01,01 SAY "CPF"
@ 01,20 GET cCpf
USE CLIENTES
APPEND BLANK
""")
        (sd / "cadprod.prg").write_text("""
TITLE "Cadastro de Produtos"
@ 01,01 SAY "Descricao"
@ 01,20 GET cDesc
USE PRODUTOS
APPEND BLANK
""")
        (sd / "schema.sql").write_text("""
CREATE TABLE CLIENTES (ID INTEGER PRIMARY KEY, CPF VARCHAR(14) NOT NULL);
CREATE TABLE PRODUTOS (ID INTEGER PRIMARY KEY, DESCRICAO VARCHAR(200) NOT NULL);
""")

        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Cadastro de Clientes", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "123.456.789-09"},
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S2",
             "screen_sample": "Cadastro de Produtos", "seq_global": 2, "norm_len": 410},
            {"type": "bytes", "key_text": "PRODUTO X"},
        ]
        capture_path = self.d / "capture.jsonl"
        capture_path.write_text("\n".join(json.dumps(e) for e in events))

        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, sd, name="title_test")
        syn.synthesize(template, samples=2, out_dir=out_dir, seed=42)

        # Verifica template
        tmpl_data = json.loads((out_dir / "template.json").read_text())
        titles = [s.get("screen_title") for s in tmpl_data.get("steps", [])]
        self.assertIn("Cadastro de Clientes", titles,
                      f"screen_title ausente no template: {titles}")
        self.assertIn("Cadastro de Produtos", titles,
                      f"screen_title ausente no template: {titles}")

        # Verifica report
        report = json.loads((out_dir / "report.json").read_text())
        report_titles = [s.get("screen_title") for s in report.get("screen_mappings", [])]
        self.assertIn("Cadastro de Clientes", report_titles,
                      f"screen_title ausente no report: {report_titles}")
        self.assertIn("Cadastro de Produtos", report_titles,
                      f"screen_title ausente no report: {report_titles}")

    # ── 5: Placeholders resolvidos ──

    def test_no_unresolved_placeholders_in_sessions(self):
        """Nenhum {{...}} deve sobrar nos values das sessoes."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        capture_path = self._make_capture_jsonl(
            ["{KEY:ENTER}", "123.456.789-09", "JOAO TESTE", "{KEY:F10}"])
        source_dir = self._make_source_dir()
        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, source_dir, name="ph_test")
        syn.synthesize(template, samples=3, out_dir=out_dir, seed=42)

        sessions_dir = out_dir / "sessions"
        for sf in sorted(sessions_dir.glob("session_*.jsonl")):
            for line in sf.read_text().split("\n"):
                if line.strip():
                    obj = json.loads(line)
                    val = str(obj.get("value", ""))
                    self.assertNotIn("{{", val,
                                     f"Placeholder nao resolvido em {sf.name}: {val}")
                    self.assertNotIn("}}", val,
                                     f"Placeholder nao resolvido em {sf.name}: {val}")

    # ── 6: Comandos preservados ──

    def test_commands_are_preserved_in_sessions(self):
        """{KEY:ENTER} e {KEY:F10} permanecem como comandos."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        capture_path = self._make_capture_jsonl(
            ["{KEY:ENTER}", "123.456.789-09", "JOAO TESTE", "{KEY:F10}"])
        source_dir = self._make_source_dir()
        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, source_dir, name="cmd_test")
        syn.synthesize(template, samples=2, out_dir=out_dir, seed=42)

        sessions_dir = out_dir / "sessions"
        for sf in sorted(sessions_dir.glob("session_*.jsonl")):
            cmds = []
            for line in sf.read_text().split("\n"):
                if line.strip():
                    obj = json.loads(line)
                    if obj.get("type") == "command":
                        cmds.append(obj["value"])
            self.assertIn("{KEY:ENTER}", cmds,
                          f"{sf.name}: ENTER nao encontrado como comando")
            self.assertIn("{KEY:F10}", cmds,
                          f"{sf.name}: F10 nao encontrado como comando")

    # ── 7: Multi-entidade com dados tipados ──

    def test_multientity_journey_generates_valid_typed_data(self):
        """CLIENTES.CPF valido, PRODUTOS.VALOR decimal, PRODUTOS.DESCRICAO presente."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        sd = self.d / "source"
        sd.mkdir()
        (sd / "cadcli.prg").write_text("""
TITLE "Cadastro de Clientes"
@ 01,01 SAY "CPF"
@ 01,20 GET cCpf
@ 02,01 SAY "Nome"
@ 02,20 GET cNome
@ 03,01 SAY "Endereco"
@ 03,20 GET cEnd
USE CLIENTES
APPEND BLANK
""")
        (sd / "cadprod.prg").write_text("""
TITLE "Cadastro de Produtos"
@ 01,01 SAY "Descricao"
@ 01,20 GET cDesc
@ 02,01 SAY "Valor"
@ 02,20 GET nValor
USE PRODUTOS
APPEND BLANK
""")
        (sd / "schema.sql").write_text("""
CREATE TABLE CLIENTES (ID INTEGER PRIMARY KEY, CPF VARCHAR(14) NOT NULL, NOME VARCHAR(100), ENDERECO VARCHAR(200));
CREATE TABLE PRODUTOS (ID INTEGER PRIMARY KEY, DESCRICAO VARCHAR(200), VALOR DECIMAL(10,2));
""")

        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Cadastro de Clientes", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "{KEY:ENTER}"},
            {"type": "bytes", "key_text": "123.456.789-09"},
            {"type": "bytes", "key_text": "JOAO CLIENTE"},
            {"type": "bytes", "key_text": "RUA TESTE"},
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S2",
             "screen_sample": "Cadastro de Produtos", "seq_global": 2, "norm_len": 410},
            {"type": "bytes", "key_text": "PRODUTO TESTE"},
            {"type": "bytes", "key_text": "10,50"},
            {"type": "bytes", "key_text": "{KEY:F10}"},
        ]
        capture_path = self.d / "capture.jsonl"
        capture_path.write_text("\n".join(json.dumps(e) for e in events))

        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, sd, name="typed_test")
        result = syn.synthesize(template, samples=5, out_dir=out_dir, seed=42)

        sessions_dir = out_dir / "sessions"
        import re
        cpf_re = re.compile(r'^\d{3}\.\d{3}\.\d{3}-\d{2}$')
        money_re = re.compile(r'^\d+([,.]\d{1,2})?$')

        # Coleta todos os pares (entity, field) das sessoes
        all_fields: list[tuple[str, str]] = []
        for sf in sorted(sessions_dir.glob("session_*.jsonl")):
            for line in sf.read_text().split("\n"):
                if not line.strip():
                    continue
                obj = json.loads(line)
                fn = (obj.get("field") or "").upper()
                en = (obj.get("entity") or "").upper()
                val = str(obj.get("value", ""))

                if fn == "CPF":
                    self.assertTrue(cpf_re.match(val), f"CPF invalido: {val}")
                elif fn == "NOME":
                    self.assertTrue(len(val) > 0 and "{{" not in val, f"NOME: {val}")
                elif fn == "ENDERECO":
                    self.assertTrue(len(val) > 0 and "{{" not in val, f"ENDERECO: {val}")
                elif fn == "DESCRICAO":
                    self.assertTrue(len(val) > 0 and "{{" not in val, f"DESCRICAO: {val}")
                elif fn == "VALOR":
                    self.assertTrue(money_re.match(val), f"VALOR nao numerico: {val}")
                    for bad in ("lorem", "ipsum", "consectetur", "amet"):
                        self.assertNotIn(bad, val.lower(), f"VALOR com texto: {val}")

                if obj.get("type") == "input" and en and fn:
                    all_fields.append((en, fn))

        # ═══ DESCRICAO e VALOR devem existir em CADA sessao ═══
        for sf in sorted(sessions_dir.glob("session_*.jsonl")):
            sess_fields: list[tuple[str, str]] = []
            for line in sf.read_text().split("\n"):
                if not line.strip():
                    continue
                obj = json.loads(line)
                en = (obj.get("entity") or "").upper()
                fn = (obj.get("field") or "").upper()
                if obj.get("type") == "input" and en and fn:
                    sess_fields.append((en, fn))

            self.assertIn(("PRODUTOS", "DESCRICAO"), sess_fields,
                          f"PRODUTOS.DESCRICAO ausente em {sf.name}")
            self.assertIn(("PRODUTOS", "VALOR"), sess_fields,
                          f"PRODUTOS.VALOR ausente em {sf.name}")

            # VALOR nao pode aparecer 2x na mesma sessao
            valor_in_session = sess_fields.count(("PRODUTOS", "VALOR"))
            self.assertEqual(valor_in_session, 1,
                             f"VALOR aparece {valor_in_session}x em {sf.name}")
            desc_in_session = sess_fields.count(("PRODUTOS", "DESCRICAO"))
            self.assertEqual(desc_in_session, 1,
                             f"DESCRICAO aparece {desc_in_session}x em {sf.name}")

    # ── v0.2.3: Valores monetarios realistas ──

    def test_product_money_values_are_realistic_by_default(self):
        """VALOR/PRECO de produto entre 1.00 e 9999.99, QUANTIDADE 1-999."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        sd = self.d / "source"
        sd.mkdir()
        (sd / "cadprod.prg").write_text("""
TITLE "Cadastro de Produtos"
@ 01,01 SAY "Descricao"
@ 01,20 GET cDesc
@ 02,01 SAY "Valor"
@ 02,20 GET nValor
@ 03,01 SAY "Preco"
@ 03,20 GET nPreco
@ 04,01 SAY "Quantidade"
@ 04,20 GET nQtd
USE PRODUTOS
APPEND BLANK
""")
        (sd / "schema.sql").write_text("""
CREATE TABLE PRODUTOS (
    ID INTEGER PRIMARY KEY,
    DESCRICAO VARCHAR(200),
    VALOR DECIMAL(10,2),
    PRECO DECIMAL(10,2),
    QUANTIDADE DECIMAL(10,2)
);
""")

        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Cadastro de Produtos", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "PRODUTO TESTE"},
            {"type": "bytes", "key_text": "10,50"},
            {"type": "bytes", "key_text": "15,90"},
            {"type": "bytes", "key_text": "100"},
        ]
        capture_path = self.d / "capture.jsonl"
        capture_path.write_text("\n".join(json.dumps(e) for e in events))

        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, sd, name="money_test")
        syn.synthesize(template, samples=10, out_dir=out_dir, seed=42)

        sessions_dir = out_dir / "sessions"
        for sf in sorted(sessions_dir.glob("session_*.jsonl")):
            for line in sf.read_text().split("\n"):
                if not line.strip():
                    continue
                obj = json.loads(line)
                fn = (obj.get("field") or "").upper()
                val_str = str(obj.get("value", ""))
                if fn in ("VALOR", "PRECO"):
                    try:
                        val = float(val_str)
                    except ValueError:
                        val = float(val_str.replace(",", "."))
                    self.assertGreaterEqual(val, 1.0,
                        f"{fn}={val} muito baixo em {sf.name}")
                    self.assertLessEqual(val, 9999.99,
                        f"{fn}={val} muito alto em {sf.name}")
                elif fn in ("QUANTIDADE", "QTD", "QTDE"):
                    try:
                        val = float(val_str)
                    except ValueError:
                        val = float(val_str.replace(",", "."))
                    self.assertGreaterEqual(val, 1.0,
                        f"{fn}={val} muito baixo")
                    self.assertLessEqual(val, 999.0,
                        f"{fn}={val} muito alto")

    # ── v0.2.4: nPreco → PRECO (nao VALOR) ──

    def test_npreco_maps_to_preco_even_when_valor_also_exists(self):
        """nPreco deve mapear para PRECO, nao para VALOR."""
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        sd = self.d / "source"
        sd.mkdir()
        (sd / "cadprod.prg").write_text("""
TITLE "Cadastro de Produtos"
@ 01,01 SAY "Descricao"
@ 01,20 GET cDescr
@ 02,01 SAY "Preco"
@ 02,20 GET nPreco
USE PRODUTOS
APPEND BLANK
""")
        (sd / "schema.sql").write_text("""
CREATE TABLE PRODUTOS (
    ID INTEGER PRIMARY KEY,
    DESCRICAO VARCHAR(200),
    VALOR DECIMAL(10,2),
    PRECO DECIMAL(10,2)
);
""")

        events = [
            {"type": "checkpoint", "session_id": "s1", "screen_sig": "S1",
             "screen_sample": "Cadastro de Produtos", "seq_global": 1, "norm_len": 400},
            {"type": "bytes", "key_text": "PRODUTO TESTE"},
            {"type": "bytes", "key_text": "10,50"},
        ]
        capture_path = self.d / "capture.jsonl"
        capture_path.write_text("\n".join(json.dumps(e) for e in events))

        out_dir = self.d / "out"
        out_dir.mkdir()

        syn = JourneySynthesizer()
        template = syn.from_capture(capture_path, sd, name="preco_test")
        result = syn.synthesize(template, samples=3, out_dir=out_dir, seed=42)

        # Verifica template: placeholders devem incluir PRECO, nao VALOR
        tmpl = json.loads((out_dir / "template.json").read_text())
        placeholders = []
        for step in tmpl.get("steps", []):
            for inp in step.get("inputs", []):
                if inp.get("placeholder"):
                    placeholders.append(inp["placeholder"])

        self.assertIn("{{PRODUTOS.DESCRICAO}}", placeholders,
                      "DESCRICAO ausente nos placeholders")
        self.assertIn("{{PRODUTOS.PRECO}}", placeholders,
                      "PRECO ausente nos placeholders")
        # VALOR nao deve aparecer para esta tela
        valor_placeholders = [p for p in placeholders if "VALOR" in p]
        self.assertEqual(len(valor_placeholders), 0,
                         f"VALOR nao deveria aparecer: {valor_placeholders}")

        # Verifica report
        report = json.loads((out_dir / "report.json").read_text())
        all_inputs = []
        for sm in report.get("screen_mappings", []):
            for inp in sm.get("inputs", []):
                all_inputs.append(inp)

        preco_inputs = [i for i in all_inputs if i.get("field_name") == "PRECO"]
        self.assertGreaterEqual(len(preco_inputs), 1, "PRECO nao encontrado no report")
        valor_inputs = [i for i in all_inputs if i.get("field_name") == "VALOR"]
        self.assertEqual(len(valor_inputs), 0, "VALOR nao deveria estar no report")

        # Verifica session
        sess = json.loads(
            "[" + (out_dir / "sessions" / "session_000001.jsonl").read_text()
            .strip().replace("\n", ",") + "]")
        fields = [(e.get("field"), e.get("entity")) for e in sess]
        self.assertIn(("DESCRICAO", "PRODUTOS"), fields)
        self.assertIn(("PRECO", "PRODUTOS"), fields)
        self.assertNotIn(("VALOR", "PRODUTOS"), fields,
                         "VALOR nao deveria aparecer na sessao")


if __name__ == "__main__":
    unittest.main()
