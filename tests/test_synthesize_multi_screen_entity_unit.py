# Testes unitários: synthesize gera dataset cobrindo TODAS as telas da entidade (P0).
#
# Regressão: o laço de geração de dataset em JourneySynthesizer.synthesize
# parava no PRIMEIRO step da entidade (``break`` após o primeiro match).
# Campos mapeados em outras telas da mesma entidade ficavam fora do dataset,
# os placeholders {{ENT.campo}} não eram resolvidos nas sessões e a própria
# validação reprovava todas as sessões (observado no AIX, captura 13:
# valid_sessions=0/10, 630 warnings de placeholder não resolvido).

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dakota_gateway.synthetic.journey_synthesizer import (
    JourneyStep,
    JourneySynthesizer,
    JourneyTemplate,
    TemplateInput,
)


def _input(field: str, original: str = "x") -> TemplateInput:
    return TemplateInput(
        original=original, placeholder="{{PED." + field + "}}",
        field_name=field, entity_name="PED",
        method="by_screen_order", confidence=0.6,
    )


def _template_multi_tela() -> JourneyTemplate:
    """Mesma entidade PED em duas telas, campos diferentes em cada uma."""
    return JourneyTemplate(
        journey_id="t2",
        name="multi",
        capture_source="cap.jsonl",
        entities_involved=["PED"],
        steps=[
            JourneyStep(
                screen_title="tela1", screen_signature="L=1;W=10",
                entity_name="PED", operation="", binding_confidence=0.5,
                inputs=[_input("CODIGO", "10")], matched_fields=["CODIGO"],
            ),
            JourneyStep(
                screen_title="tela2", screen_signature="L=2;W=20",
                entity_name="PED", operation="", binding_confidence=0.5,
                inputs=[_input("QTDE", "5"), _input("PRECO", "9,99")],
                matched_fields=["QTDE", "PRECO"],
            ),
        ],
        evidence=[],
    )


class SynthesizeMultiScreenEntityTest(unittest.TestCase):
    def test_dataset_cobre_campos_de_todas_as_telas(self):
        """Campos da 2ª tela da entidade também entram no dataset."""
        with TemporaryDirectory() as td:
            js = JourneySynthesizer()
            result = js.synthesize(
                _template_multi_tela(), samples=3, out_dir=Path(td), seed=42)
            records = [json.loads(l) for l in
                       Path(result.dataset_path).read_text(encoding="utf-8").splitlines()
                       if l.strip()]
        self.assertTrue(records, "dataset vazio")
        keys = set().union(*(r.keys() for r in records)) - {"_entity"}
        self.assertIn("CODIGO", keys)
        self.assertIn("QTDE", keys)
        self.assertIn("PRECO", keys)

    def test_sessoes_sem_placeholder_pendente(self):
        """Todas as sessões geradas passam na própria validação."""
        with TemporaryDirectory() as td:
            js = JourneySynthesizer()
            template = _template_multi_tela()
            result = js.synthesize(template, samples=3, out_dir=Path(td), seed=42)
            validation = js.validate_sessions(Path(result.sessions_dir), template)
        self.assertEqual(validation["total_sessions"], 3)
        self.assertEqual(validation["valid_sessions"], 3)
        self.assertEqual(validation["unresolved_placeholders"], [])


if __name__ == "__main__":
    unittest.main()
