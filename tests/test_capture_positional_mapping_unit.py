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
        assert mi.method == "menu_option_kept"
        assert mi.evidence
        assert enr.mapped_inputs == 0

    def test_menu_option_em_get_fora_da_entidade_e_mantido(
            self, prg_file, binding, tmp_path):
        """Cursor num GET do fonte cujo campo NÃO existe na entidade da KB
        (ex.: entidade espúria "arq" sem 'ecommerc' — captura 13): o valor
        é mantido com evidência explícita, não method vazio."""
        sem_frete = EntityDefinition(
            name="PED", storage_type="sql",
            fields=[FieldDefinition(name="pedido", datatype="text")])
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["1"], [(7, 13)])  # GET cFrete existe no fonte
        enr = integ.enrich_template(tmpl, [sem_frete], [binding])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert not mi.field_name
        assert not mi.placeholder
        assert mi.method == "kept_layout_field"
        assert mi.layout_field == "frete"
        assert any("ausente na entidade" in ev for ev in mi.evidence)
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


PRG_GRID = """\
@ 04,00 say [*] + fTraduz(p_idioma,"Pedido","P",12,.t.,"")
@ 07,01 say fTraduz(p_idioma,"Frete","P",12,.t.,"")
@ 04,13 get cPedido pict p_mascdoc
@ 07,13 get cFrete  pict "@!"
clear gets
dbedit(16,00,21,69,vCamTmp,"fTabela",vPictTmp,vColTmp)
release vCamTmp
public vCamTmp[03]
vCamTmp[01] = "right(item,2)"
vCamTmp[02] = "modelo"
vCamTmp[03] = "qtd"
vPictTmp[01] = "99"
vPictTmp[02] = "@"
vPictTmp[03] = "9999"
vColTmp[01] = fTraduz(p_idioma,"Item","P",2,.f.,"")
vColTmp[02] = fTraduz(p_idioma,"Modelo","P",6,.f.,"")
vColTmp[03] = fTraduz(p_idioma,"Qtd","P",3,.f.,"")
"""


class TestGridMapping:
    """Grade dbedit: inputs digitados em células da grade (sem @ GET) são
    mapeados pela coluna — caso da captura 13 (itens do pedido)."""

    @pytest.fixture
    def prg_grid(self, tmp_path):
        prg = tmp_path / "xx361.prg"
        prg.write_text(PRG_GRID, encoding="utf-8")
        return prg

    @pytest.fixture
    def entity_grid(self):
        return EntityDefinition(
            name="PED", storage_type="sql",
            fields=[FieldDefinition(name="pedido", datatype="text"),
                    FieldDefinition(name="modelo", datatype="text"),
                    FieldDefinition(name="qtd", datatype="integer")])

    @pytest.fixture
    def binding_grid(self, prg_grid):
        return ScreenEntityBinding(
            program_name="pXx361", source_file=str(prg_grid),
            entity_name="PED", operation="read", confidence=0.6,
            matched_fields=["pedido"])

    def test_input_em_celula_de_grade_mapeia_coluna(
            self, prg_grid, entity_grid, binding_grid, tmp_path):
        """'g2511' digitado na célula modelo da grade (19,4)."""
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["g2511"], [(19, 4)])
        enr = integ.enrich_template(tmpl, [entity_grid], [binding_grid])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert mi.method == "by_grid_column"
        assert mi.field_name == "modelo"
        assert mi.placeholder == "{{PED.modelo}}"

    def test_menu_option_em_celula_numerica_vira_dado(
            self, prg_grid, entity_grid, binding_grid, tmp_path):
        """'1' na célula qtd (10-13, PICTURE 9999) é quantidade, não menu."""
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["1"], [(17, 12)])
        enr = integ.enrich_template(tmpl, [entity_grid], [binding_grid])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert mi.method == "by_grid_column"
        assert mi.field_name == "qtd"

    def test_tecla_mais_e_comando_mesmo_sobre_grade(
            self, prg_grid, entity_grid, binding_grid, tmp_path):
        """'+' confirma o registro no dbedit ("<+> Confirma") — nunca é
        dado da célula sob o cursor."""
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["+"], [(19, 4)])
        enr = integ.enrich_template(tmpl, [entity_grid], [binding_grid])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert mi.method == "command"
        assert not mi.field_name

    def test_texto_sobre_celula_numerica_nao_mapeia(
            self, prg_grid, entity_grid, binding_grid, tmp_path):
        """Valor incompatível com a PICTURE da célula (texto em célula
        numérica) não é dado dela — cursor estava só de passagem."""
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["i"], [(17, 12)])  # qtd é 9999
        enr = integ.enrich_template(tmpl, [entity_grid], [binding_grid])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert not mi.field_name


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


PRG_GRID_SRC = """\
@ 04,00 say [*] + fTraduz(p_idioma,"Pedido","P",12,.t.,"")
@ 07,01 say fTraduz(p_idioma,"Frete","P",12,.t.,"")
@ 04,13 get cPedido pict p_mascdoc
@ 07,13 get cFrete  pict "@!"
clear gets
use &earqtmp1 in 0 alias arqtmp1
public vCamTmp[03]
vCamTmp[01] = "right(item,2)"
vCamTmp[02] = "modelo"
vCamTmp[03] = "qtd"
vPictTmp[01] = "99"
vPictTmp[02] = "@"
vPictTmp[03] = "9999"
vColTmp[01] = fTraduz(p_idioma,"Item","P",2,.f.,"")
vColTmp[02] = fTraduz(p_idioma,"Modelo","P",6,.f.,"")
vColTmp[03] = fTraduz(p_idioma,"Qtd","P",3,.f.,"")
if seek(cPedido,est361)
   do while !eof()
      select arqtmp1
      append blank
      replace modelo with est361->modelo
      replace qtd    with est361->qtd
   enddo
endif
dbedit(16,00,21,69,vCamTmp,"fTabela",vPictTmp,vColTmp)
"""


class TestGridSourceMapping:
    """Célula de grade cuja tabela de origem é conhecida (``with est361->``
    no preenchimento do alias tmp) é mapeada para a ENTIDADE DA GRADE, não
    para a entidade da tela — mesmo quando o campo não existe na KB (a KB
    da captura 13 tem est361/est366 sem campos)."""

    @pytest.fixture
    def prg_src(self, tmp_path):
        prg = tmp_path / "xx361.prg"
        prg.write_text(PRG_GRID_SRC, encoding="utf-8")
        return prg

    @pytest.fixture
    def entity_sem_itens(self):
        # Entidade da tela SEM os campos da grade (modelo/qtd não estão na
        # KB) — o mapeamento não pode depender da KB.
        return EntityDefinition(
            name="PED", storage_type="sql",
            fields=[FieldDefinition(name="pedido", datatype="text")])

    @pytest.fixture
    def binding_src(self, prg_src):
        return ScreenEntityBinding(
            program_name="pXx361", source_file=str(prg_src),
            entity_name="PED", operation="read", confidence=0.6,
            matched_fields=["pedido"])

    def test_celula_de_grade_vai_para_entidade_da_tabela(
            self, prg_src, entity_sem_itens, binding_src, tmp_path):
        """'g2511' na célula modelo (19,4): campo ausente na KB, mas a
        tabela da grade (est361) é conhecida → by_grid_source."""
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["g2511"], [(19, 4)])
        enr = integ.enrich_template(tmpl, [entity_sem_itens], [binding_src])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert mi.method == "by_grid_source"
        assert (mi.entity_name, mi.field_name) == ("est361", "modelo")
        assert mi.placeholder == "{{est361.modelo}}"
        assert mi.is_grid and mi.grid_source == "est361"
        # A entidade da grade entra na geração do dataset — senão o
        # placeholder ficaria sem valor.
        assert "est361" in {e.lower() for e in enr.entities_involved}

    def test_menu_option_em_celula_de_grade_com_origem(
            self, prg_src, entity_sem_itens, binding_src, tmp_path):
        """'1' na célula qtd (PICTURE 9999) vira dado da entidade da grade
        (número), não opção de menu mantida."""
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["1"], [(17, 12)])
        enr = integ.enrich_template(tmpl, [entity_sem_itens], [binding_src])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        assert mi.method == "by_grid_source"
        assert (mi.entity_name, mi.field_name) == ("est361", "qtd")
        assert mi.field_datatype == "number"

    def test_grade_sem_origem_cai_no_comportamento_anterior(
            self, tmp_path):
        """Sem ``with X->`` identificável, grid_source fica vazio e o campo
        fora da KB continua mantido (kept_layout_field) — não inventa."""
        prg = tmp_path / "xx361.prg"
        prg.write_text(PRG_GRID, encoding="utf-8")
        entity = EntityDefinition(
            name="PED", storage_type="sql",
            fields=[FieldDefinition(name="pedido", datatype="text")])
        binding = ScreenEntityBinding(
            program_name="pXx361", source_file=str(prg),
            entity_name="PED", operation="read", confidence=0.6,
            matched_fields=["pedido"])
        integ = CaptureKnowledgeIntegrator(source_root=str(tmp_path))
        tmpl = _template(["g2511"], [(19, 4)])
        enr = integ.enrich_template(tmpl, [entity], [binding])
        mi = enr.screen_mappings[0].mapped_inputs[0]
        # Não inventa: sem tabela de origem, a célula fora da KB não vira
        # substituição (sem placeholder, sem campo).
        assert mi.method != "by_grid_source"
        assert not mi.field_name and not mi.placeholder
