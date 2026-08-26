# Testes unitários: formato dos valores sintéticos gerados para campos de grade.
#
# Regressão (captura 13, AIX, v0.8.32): os itens do pedido digitados na grade
# dbedit do est361 saíam no de→para com formato incompatível com a PICTURE da
# coluna — qtd (PICTURE "9999") virou decimal "554,50", comb ("@") virou
# "826683,90" e modelo ("@") virou nome de pessoa "Gabriela Duarte". Num replay
# real isso estoura a largura da coluna e desloca a grade.
#
# Regras garantidas aqui:
# 1. PICTURE literal vence o range heurístico de geração (qtd 9999 → inteiro);
# 2. original inteiro puro → datatype number (não decimal);
# 3. PICTURE de função ("@") + original alfanumérico curto → formato
#    "pattern:" que preserva o shape do original (letra→letra, dígito→dígito);
# 4. PICTURE decimal BR ("9.999,99") continua decimal dentro do range.

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dakota_gateway.synthetic.journey_synthesizer import (
    JourneyStep,
    JourneySynthesizer,
    JourneyTemplate,
    TemplateInput,
)


def _input(field: str, original: str, original_type: str, picture: str) -> TemplateInput:
    return TemplateInput(
        original=original, placeholder="{{PED." + field + "}}",
        field_name=field, entity_name="PED",
        method="by_grid_column", confidence=0.9,
        original_type=original_type, picture=picture,
    )


def _template_grade() -> JourneyTemplate:
    return JourneyTemplate(
        journey_id="tg",
        name="grade",
        capture_source="cap.jsonl",
        entities_involved=["PED"],
        steps=[
            JourneyStep(
                screen_title="itens", screen_signature="L=1;W=10",
                entity_name="PED", operation="", binding_confidence=0.9,
                inputs=[
                    _input("QTD", "1", "number", "9999"),
                    _input("COMB", "0000135", "number", "@"),
                    _input("MODELO", "g2511", "text", "@"),
                    _input("VALOR", "229,9", "number", "9.999,99"),
                ],
                matched_fields=["QTD", "COMB", "MODELO", "VALOR"],
            ),
        ],
        evidence=[],
    )


def _records(result) -> list[dict]:
    return [json.loads(l) for l in
            Path(result.dataset_path).read_text(encoding="utf-8").splitlines()
            if l.strip()]


class SynthesizeGridFormatTest(unittest.TestCase):
    def test_qtd_picture_9999_gera_inteiro(self):
        """PICTURE 9999 vence o range heurístico: inteiro 0..9999, nunca decimal."""
        with TemporaryDirectory() as td:
            result = JourneySynthesizer().synthesize(
                _template_grade(), samples=8, out_dir=Path(td), seed=42)
            records = _records(result)
        self.assertTrue(records)
        for r in records:
            v = r["QTD"]
            self.assertIsInstance(v, int, f"QTD deveria ser int, veio {v!r}")
            self.assertGreaterEqual(v, 0)
            self.assertLessEqual(v, 9999)

    def test_comb_picture_funcao_preserva_shape_numerico(self):
        """comb ('@') com original '0000135' → 7 dígitos, não decimal com vírgula."""
        with TemporaryDirectory() as td:
            result = JourneySynthesizer().synthesize(
                _template_grade(), samples=8, out_dir=Path(td), seed=42)
            records = _records(result)
        for r in records:
            self.assertRegex(str(r["COMB"]), r"^\d{7}$")

    def test_modelo_picture_funcao_preserva_shape_alfanumerico(self):
        """modelo ('@') com original 'g2511' → letra+4 dígitos, não nome de pessoa."""
        with TemporaryDirectory() as td:
            result = JourneySynthesizer().synthesize(
                _template_grade(), samples=8, out_dir=Path(td), seed=42)
            records = _records(result)
        for r in records:
            self.assertRegex(str(r["MODELO"]), r"^[a-z]\d{4}$")

    def test_valor_picture_decimal_br_continua_decimal(self):
        """PICTURE 9.999,99 → decimal dentro do range."""
        with TemporaryDirectory() as td:
            result = JourneySynthesizer().synthesize(
                _template_grade(), samples=8, out_dir=Path(td), seed=42)
            records = _records(result)
        for r in records:
            v = float(r["VALOR"])
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 9999.99)

    def test_pattern_deterministico_por_seed(self):
        """Mesma semente → mesmos valores pattern (reprodutibilidade)."""
        with TemporaryDirectory() as td1, TemporaryDirectory() as td2:
            js = JourneySynthesizer()
            r1 = _records(js.synthesize(_template_grade(), 4, Path(td1), seed=7))
            r2 = _records(js.synthesize(_template_grade(), 4, Path(td2), seed=7))
        self.assertEqual([r["MODELO"] for r in r1], [r["MODELO"] for r in r2])


if __name__ == "__main__":
    unittest.main()
