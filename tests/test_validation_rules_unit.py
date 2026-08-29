# Testes unitários: VALID do fonte → constraints de geração + FK com valores
# reais (lookup_values).
#
# Cobre:
# 1. parse_valid_expr — comparações (> >= < <=), !empty, inlist/$, expressão
#    não reconhecida ignorada;
# 2. apply_valid_constraints — interseção (nunca relaxa PICTURE/clamp), passo
#    de comparação estrita por tipo (int=1, decimal=0,01), choices;
# 3. screen_layout — VALID extraído da linha do GET;
# 4. field_classifier — valid_expr vira min/max/domain na classificação da KB;
# 5. synthesize — VALID vence a heurística de nome e respeita a PICTURE;
# 6. suggest_key_fields — FK coberta por lookup_values deixa de ser âncora;
#    FK sem cobertura continua âncora; único/índice continuam âncora;
# 7. dataset_builder — campo com lookup sorteia valores reais da lista.

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dakota_gateway.synthetic.validation_rules import (
    apply_valid_constraints,
    parse_valid_expr,
    valid_lookup_table,
)
from dakota_gateway.synthetic.schema import FieldSchema


class ValidLookupTableTests(unittest.TestCase):
    """fValida(chave, chave, [tabela], ...) → tabela de cadastro (FK)."""

    def test_extrai_tabela_do_fvalida(self):
        expr = ("lastkey() = 5 or fValida(strzero(cFrete,2),"
                "strzero(cFrete,2),[arqfrete],[descricao],7,16,[])")
        self.assertEqual(valid_lookup_table(expr), "arqfrete")

    def test_primeiro_colchete_nao_vazio(self):
        expr = "fValida(cCod, cCod, [est281], [descricao], 8, 16, [], 20)"
        self.assertEqual(valid_lookup_table(expr), "est281")

    def test_sem_fvalida_retorna_vazio(self):
        self.assertEqual(valid_lookup_table("nVolumes > 0"), "")
        self.assertEqual(valid_lookup_table(""), "")

    def test_fvalida_sem_tabela_retorna_vazio(self):
        self.assertEqual(valid_lookup_table("fValida(cCod, cCod, [])"), "")

    def test_nao_e_constraint_de_faixa(self):
        # fValida é FK, não regra de faixa — parse_valid_expr não inventa
        # min/max a partir dela
        expr = ("lastkey() = 5 or fValida(strzero(cFrete,2),"
                "strzero(cFrete,2),[arqfrete],[descricao],7,16,[])")
        self.assertEqual(parse_valid_expr(expr), {})


class ParseValidExprTests(unittest.TestCase):
    def test_maior_que_zero(self):
        c = parse_valid_expr("valor > 0")
        self.assertEqual(c["min_value"], 0)
        self.assertTrue(c["min_exclusive"])

    def test_maior_igual(self):
        c = parse_valid_expr("vQtd >= 1")
        self.assertEqual(c["min_value"], 1)
        self.assertFalse(c["min_exclusive"])

    def test_menor_que(self):
        c = parse_valid_expr("perc < 100")
        self.assertEqual(c["max_value"], 100)
        self.assertTrue(c["max_exclusive"])

    def test_faixa_com_and(self):
        c = parse_valid_expr("valor > 0 .and. valor <= 9999.99")
        self.assertEqual(c["min_value"], 0)
        self.assertTrue(c["min_exclusive"])
        self.assertEqual(c["max_value"], 9999.99)
        self.assertFalse(c["max_exclusive"])

    def test_not_empty(self):
        self.assertTrue(parse_valid_expr("!empty(cNome)")["required"])
        self.assertTrue(parse_valid_expr(".not. empty(cNome)")["required"])

    def test_inlist(self):
        c = parse_valid_expr('inlist(cTipo, "A", "B", "C")')
        self.assertEqual(c["choices"], ["A", "B", "C"])

    def test_contains_char(self):
        c = parse_valid_expr('cSN $ "SN"')
        self.assertEqual(c["choices"], ["S", "N"])

    def test_expressao_nao_reconhecida_e_ignorada(self):
        self.assertEqual(parse_valid_expr("pEst361ConsFormaPag()"), {})
        self.assertEqual(parse_valid_expr(""), {})
        self.assertEqual(parse_valid_expr(None), {})


class ApplyValidConstraintsTests(unittest.TestCase):
    def test_min_estrito_inteiro(self):
        fs = FieldSchema(name="QTD", datatype="number")
        self.assertTrue(apply_valid_constraints(fs, parse_valid_expr("qtd > 0")))
        self.assertEqual(fs.min_value, 1)

    def test_min_estrito_decimal(self):
        fs = FieldSchema(name="VALOR", datatype="decimal")
        apply_valid_constraints(fs, parse_valid_expr("valor > 0"))
        self.assertAlmostEqual(fs.min_value, 0.01)

    def test_intersecao_nao_relaxa_picture(self):
        """PICTURE 99 → max 99; VALID <= 999 não pode relaxar para 999."""
        fs = FieldSchema(name="COD", datatype="number", min_value=0, max_value=99)
        apply_valid_constraints(fs, parse_valid_expr("cod >= 1 .and. cod <= 999"))
        self.assertEqual(fs.min_value, 1)
        self.assertEqual(fs.max_value, 99)

    def test_min_maior_que_max_nao_inverte(self):
        fs = FieldSchema(name="X", datatype="number", min_value=0, max_value=9)
        apply_valid_constraints(fs, parse_valid_expr("x > 100"))
        # min=101 > max=9 seria inválido — não aplica
        self.assertEqual(fs.min_value, 0)

    def test_choices_nao_sobrescreve_dominio_existente(self):
        fs = FieldSchema(name="SN", datatype="text", choices=["S", "N"])
        apply_valid_constraints(fs, parse_valid_expr('inlist(sn, "A", "B")'))
        self.assertEqual(fs.choices, ["S", "N"])

    def test_required(self):
        fs = FieldSchema(name="NOME", datatype="text", required=False)
        apply_valid_constraints(fs, parse_valid_expr("!empty(nome)"))
        self.assertTrue(fs.required)


class ScreenLayoutValidTests(unittest.TestCase):
    def test_valid_extraido_do_get(self):
        from dakota_gateway.synthetic.screen_layout import extract_layout

        with TemporaryDirectory() as td:
            prg = Path(td) / "x.prg"
            prg.write_text(
                '@ 06,01 say "Valor:"\n'
                '@ 06,13 get nValor pict "999.999,99" valid nValor > 0\n'
                '@ 07,13 get cNome valid !empty(cNome) when lEdit\n',
                encoding="utf-8")
            by_var = {pf.var: pf for pf in extract_layout(prg)}
        self.assertEqual(by_var["nValor"].valid_expr, "nValor > 0")
        self.assertEqual(by_var["nValor"].picture, "999.999,99")
        # cláusula WHEN não vaza para a expressão VALID
        self.assertEqual(by_var["cNome"].valid_expr, "!empty(cNome)")


class FieldClassifierValidTests(unittest.TestCase):
    def test_valid_expr_vira_min_e_dominio(self):
        from dakota_gateway.source_analyzer.entity_catalog import FieldDefinition
        from dakota_gateway.source_analyzer.field_classifier import FieldClassifier

        fc = FieldClassifier.classify(FieldDefinition(
            name="VALOR", datatype="decimal", valid_expr="valor > 0"))
        self.assertGreaterEqual(fc.min_value, 1)

        fc2 = FieldClassifier.classify(FieldDefinition(
            name="TIPO", datatype="text",
            valid_expr='inlist(tipo, "A", "V")'))
        self.assertEqual(fc2.domain_values, ["A", "V"])


class SynthesizeValidTests(unittest.TestCase):
    """VALID aplicado na geração: vence a heurística de nome, respeita PICTURE."""

    def _template(self, valid_expr: str, picture: str = "") -> "object":
        from dakota_gateway.synthetic.journey_synthesizer import (
            JourneyStep, JourneyTemplate, TemplateInput)

        return JourneyTemplate(
            journey_id="tv", name="valid", capture_source="cap.jsonl",
            entities_involved=["PED"],
            steps=[JourneyStep(
                screen_title="itens", screen_signature="L=1;W=10",
                entity_name="PED", operation="", binding_confidence=0.9,
                inputs=[TemplateInput(
                    original="229,9", placeholder="{{PED.VALOR}}",
                    field_name="VALOR", entity_name="PED",
                    method="by_grid_column", confidence=0.9,
                    original_type="number", picture=picture,
                    valid_expr=valid_expr)],
                matched_fields=["VALOR"],
            )],
        )

    def _records(self, result):
        return [json.loads(l) for l in
                Path(result.dataset_path).read_text(encoding="utf-8").splitlines()
                if l.strip()]

    def test_valid_maior_que_zero_na_geracao(self):
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        with TemporaryDirectory() as td:
            result = JourneySynthesizer().synthesize(
                self._template("valor > 0", picture="9.999,99"),
                samples=10, out_dir=Path(td), seed=42)
            records = self._records(result)
        for r in records:
            self.assertGreater(float(r["VALOR"]), 0)

    def test_report_carrega_valid_expr(self):
        from dakota_gateway.synthetic.journey_synthesizer import JourneySynthesizer

        with TemporaryDirectory() as td:
            result = JourneySynthesizer().synthesize(
                self._template("valor > 0"), samples=2, out_dir=Path(td), seed=42)
            report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
        inp = report["screen_mappings"][0]["inputs"][0]
        self.assertEqual(inp["valid_expr"], "valor > 0")


class LookupCoveredAnchorTests(unittest.TestCase):
    """FK coberta por valores reais deixa de ser âncora; demais seguem."""

    def _entity(self):
        import sys
        GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
        for p in (str(GATEWAY_DIR), str(GATEWAY_DIR / "control")):
            if p not in sys.path:
                sys.path.insert(0, p)
        from dakota_gateway.source_analyzer.entity_catalog import (
            EntityDefinition, FieldDefinition)

        return EntityDefinition(
            name="PEDIDOS",
            fields=[
                FieldDefinition(name="CONDPAG", datatype="text",
                                lookup_table="CONDPAGTO"),
                FieldDefinition(name="NUMERO", datatype="text", unique_flag=True),
            ],
        )

    def _mappings(self):
        return [{
            "entity_name": "PEDIDOS",
            "inputs": [
                {"original": "15", "field_name": "condpag",
                 "placeholder": "{{pedidos.condpag}}"},
                {"original": "D0001", "field_name": "numero",
                 "placeholder": "{{pedidos.numero}}"},
            ],
        }]

    def test_lookup_sem_cobertura_e_ancora(self):
        from control.services.capture_synthesis_service import suggest_key_fields
        keys = suggest_key_fields(self._mappings(), [self._entity()])
        self.assertIn("condpag", keys)

    def test_lookup_coberto_deixa_de_ser_ancora(self):
        from control.services.capture_synthesis_service import suggest_key_fields
        keys = suggest_key_fields(
            self._mappings(), [self._entity()],
            lookup_covered={"condpagto"})
        self.assertNotIn("condpag", keys)
        # unique continua âncora mesmo com cobertura de lookup
        self.assertIn("numero", keys)


class DatasetLookupValuesTests(unittest.TestCase):
    def test_campo_lookup_sorteia_da_lista_real(self):
        from dakota_gateway.synthetic.dataset_builder import DatasetBuilder
        from dakota_gateway.synthetic.schema import (
            FieldSchema, ScreenSchema, SyntheticSchema)

        schema = SyntheticSchema(
            screen=ScreenSchema(
                screen_signature="PED", title="PED", program_name="PED",
                fields=[FieldSchema(name="CONDPAG", datatype="number",
                                    lookup="condpagto")]),
            entity_name="PED", quantity=8, seed=42)
        ds = DatasetBuilder().build(
            schema, lookup_values={"condpagto": [2, 15, 28]})
        for rec in ds.records:
            self.assertIn(rec.data["CONDPAG"], [2, 15, 28])


if __name__ == "__main__":
    unittest.main()
