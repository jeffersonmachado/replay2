# Testes unitários: validate_sessions tolera "field": null nas sessões (P0).
#
# Regressão: sessões sintéticas gravam inputs de navegação com "field": null
# explícito. ``obj.get("field", "?")`` devolve None nesse caso (a chave
# existe), e ``sorted(field_counts.keys())`` explodia com
# ``TypeError: '<' not supported between instances of 'str' and 'NoneType'``
# — derrubava o request do synthesize DEPOIS de todo o trabalho pesado
# (parse/síntese), perdendo a resposta ao cliente.

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


def _template() -> JourneyTemplate:
    return JourneyTemplate(
        journey_id="t1",
        name="t",
        capture_source="cap.jsonl",
        entities_involved=["CLI"],
        steps=[JourneyStep(
            screen_title="tela",
            screen_signature="L=1;W=10",
            entity_name="CLI",
            operation="",
            binding_confidence=0.5,
            inputs=[
                TemplateInput(original="abc", placeholder="{{CLI_NOME}}",
                              field_name="NOME", entity_name="CLI",
                              method="by_field_name", confidence=0.9),
                TemplateInput(original="\r", placeholder=None,
                              field_name=None, entity_name=None,
                              method="unmapped", confidence=0.0),
            ],
            matched_fields=["NOME"],
        )],
        evidence=[],
    )


class ValidateSessionsNullFieldTest(unittest.TestCase):
    def test_field_null_nao_derruba_validacao(self):
        """Sessão com "field": null (navegação) não pode quebrar o sorted()."""
        with TemporaryDirectory() as td:
            sess_dir = Path(td)
            linhas = [
                {"seq": 1, "type": "input", "value": "Fulano",
                 "field": "NOME", "entity": "CLI"},
                {"seq": 2, "type": "input", "value": "\r",
                 "field": None, "entity": None},
            ]
            (sess_dir / "session_000001.jsonl").write_text(
                "\n".join(json.dumps(l) for l in linhas), encoding="utf-8")
            out = JourneySynthesizer().validate_sessions(sess_dir, _template())
        self.assertEqual(out["total_sessions"], 1)
        self.assertEqual(out["valid_sessions"], 1)
        self.assertIn("NOME", out["field_coverage"]["found"])

    def test_value_null_em_command_nao_derruba_validacao(self):
        """Linha de comando com value null também não pode quebrar o sorted()."""
        with TemporaryDirectory() as td:
            sess_dir = Path(td)
            linhas = [
                {"seq": 1, "type": "command", "value": None},
                {"seq": 2, "type": "input", "value": "x", "field": "NOME"},
            ]
            (sess_dir / "session_000001.jsonl").write_text(
                "\n".join(json.dumps(l) for l in linhas), encoding="utf-8")
            out = JourneySynthesizer().validate_sessions(sess_dir, _template())
        self.assertEqual(out["total_sessions"], 1)


if __name__ == "__main__":
    unittest.main()
