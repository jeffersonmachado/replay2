"""Testes do mapeamento posicional cursor↔GET no CaptureKnowledgeIntegrator.

Cobre o fluxo da captura 13: tela sem binding por título/programa é vinculada
ao fonte pelo código de menu (3.6.1 → xx361.prg) + labels posicionados, e os
inputs são mapeados pela posição do cursor no instante da digitação.
"""
from __future__ import annotations

import pytest

from dakota_gateway.source_analyzer.entity_catalog import (
    EntityDefinition, FieldDefinition,
)
from dakota_gateway.source_analyzer.screen_entity_linker import ScreenEntityBinding
from dakota_gateway.synthetic.capture_knowledge_integrator import (
    CaptureKnowledgeIntegrator,
)
from dakota_gateway.synthetic.capture_parametrizer import CaptureTemplate

PRG = """\
@ 04,00 say [*] + fTraduz(p_idioma,"Pedido","P",12,.t.,"")
@ 07,01 say fTraduz(p_idioma,"Frete","P",12,.t.,"")
@ 04,13 get cPedido pict p_mascdoc
@ 07,13 get cFrete  pict "@!"
clear gets
"""

SAMPLE = (
    " DAKOTA S/A                                        ESTOQUE\n"
    "  REDE DE LOJAS          | 3.6.1 PEDIDO E-COMMERCE\n"
    " Pedido.....:            Frete......:\n"
)


@pytest.fixture
def prg_file(tmp_path):
    prg = tmp_path / "xx361.prg"
    prg.write_text(PRG, encoding="utf-8")
    return prg


@pytest.fixture
def entity():
    return EntityDefinition(
        name="PED", storage_type="sql",
        fields=[FieldDefinition(name="pedido", datatype="text"),
                FieldDefinition(name="frete", datatype="text")])


@pytest.fixture
def binding(prg_file):
    return ScreenEntityBinding(
        program_name="pXx361", source_file=str(prg_file),
        entity_name="PED", operation="read", confidence=0.6,
        matched_fields=["pedido", "frete"])


def _template(inputs, positions, sample=SAMPLE):
    return CaptureTemplate(
        capture_source="cap.jsonl", session_id="s1",
        screen_sequence=["sig1"], input_templates=[],
        screen_contexts=[{
            "screen_sig": "sig1", "screen_sample": sample,
            "inputs": inputs, "input_positions": positions,
        }])


class TestPositionalBinding:
    def test_cursor_mapeia_campo(self, prg_file, entity, binding, tmp_path):
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["400"], [(4, 13)])
        enr = integ.enrich_template(tmpl, [entity], [binding])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert mi.method == "by_cursor_position"
        assert (mi.entity_name, mi.field_name) == ("PED", "pedido")
        assert mi.placeholder == "{{PED.pedido}}"
        assert enr.mapped_inputs == 1

    def test_menu_option_em_posicao_exata_de_get_vira_dado(
            self, prg_file, entity, binding, tmp_path):
        """'1' digitado exatamente no GET cFrete é dado, não opção de menu."""
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["1"], [(7, 13)])
        enr = integ.enrich_template(tmpl, [entity], [binding])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert mi.method == "by_cursor_position"
        assert mi.field_name == "frete"

    def test_menu_option_fora_de_get_permanece_menu(
            self, prg_file, entity, binding, tmp_path):
        """'9' digitado onde não há GET continua opção de menu preservada."""
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["9"], [(10, 10)])
        enr = integ.enrich_template(tmpl, [entity], [binding])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert not mi.field_name
        assert mi.original_type == "menu_option"
        assert enr.mapped_inputs == 0

    def test_sem_source_root_nao_ha_posicional(
            self, prg_file, entity, binding):
        integ = CaptureKnowledgeIntegrator()
        tmpl = _template(["400"], [(4, 13)])
        enr = integ.enrich_template(tmpl, [entity], [binding])
        assert enr.mapped_inputs == 0

    def test_posicional_substitui_fallback_de_texto(
            self, prg_file, binding, tmp_path):
        """Tela que cita 'CONSUMIDOR' no texto: o vínculo posicional (menu +
        labels) é evidência mais forte que o nome da entidade no sample."""
        consumidor = EntityDefinition(
            name="CONSUMIDOR", storage_type="sql",
            fields=[FieldDefinition(name="nome", datatype="text")])
        ped = EntityDefinition(
            name="PED", storage_type="sql",
            fields=[FieldDefinition(name="pedido", datatype="text")])
        sample = SAMPLE + " Consumidor.:\n"
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["400"], [(4, 13)], sample=sample)
        enr = integ.enrich_template(tmpl, [consumidor, ped], [binding])
        sm = enr.screen_mappings[0]
        assert sm.entity_name == "PED"
        assert sm.mapped_inputs[0].field_name == "pedido"

    def test_matched_fields_globais_nao_vazam_no_posicional(
            self, prg_file, entity, binding, tmp_path):
        """Input sem posição de GET não cai no matched_fields global do
        binding (que desalinha por tela) — permanece com o valor original."""
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["xyz"], [(21, 71)])  # posição sem GET
        enr = integ.enrich_template(tmpl, [entity], [binding])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert not mi.field_name
        assert mi.original_value == "xyz"


class TestPictureConstraints:
    def test_pict_numerica_limita_geracao(self):
        from dakota_gateway.synthetic.schema import FieldSchema
        from dakota_gateway.synthetic.screen_layout import PositionedField
        schemas = [FieldSchema(name="situacao", datatype="text"),
                   FieldSchema(name="total", datatype="text"),
                   FieldSchema(name="cpf", datatype="cpf", format="cpf")]
        layout = [
            PositionedField(row=8, col=13, var="cSituacao",
                            field="situacao", picture="99"),
            PositionedField(row=6, col=38, var="nTotal",
                            field="total", picture="999.999,99"),
            PositionedField(row=5, col=13, var="cCPF",
                            field="cpf", picture="@R 999.999.999-99"),
        ]
        CaptureKnowledgeIntegrator._apply_picture_constraints(schemas, layout)
        sit = schemas[0]
        assert sit.datatype == "number"
        assert sit.min_value == 0 and sit.max_value == 99
        tot = schemas[1]
        assert tot.datatype == "decimal" and tot.max_value == 999999.0
        # formato semântico (cpf) tem prioridade — não é tocado
        assert schemas[2].format == "cpf" and schemas[2].max_value is None

    def test_pict_funcao_ou_ausente_nao_anota(self):
        from dakota_gateway.synthetic.schema import FieldSchema
        from dakota_gateway.synthetic.screen_layout import PositionedField
        schemas = [FieldSchema(name="frete", datatype="text")]
        layout = [PositionedField(row=7, col=13, var="cFrete",
                                  field="frete", picture="@!")]
        CaptureKnowledgeIntegrator._apply_picture_constraints(schemas, layout)
        assert schemas[0].datatype == "text" and schemas[0].max_value is None
