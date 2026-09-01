#!/usr/bin/env python3
"""Testes unitários da detecção automática de campos-âncora (generalista).

Um campo-âncora é chave de consulta da entidade (índice, operação de busca
ou campo único). Substituir uma âncora por valor sintético inexistente faz a
consulta não achar o registro e desvia o fluxo gravado (ex.: cai no cadastro
em vez de seguir a jornada). Por isso âncoras são mantidas com o valor
original da captura por default — sem o usuário precisar conhecer o domínio.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))
sys.path.insert(0, str(GATEWAY_DIR / "control"))

from dakota_gateway.source_analyzer.entity_catalog import (
    EntityDefinition,
    FieldDefinition,
    OperationDefinition,
)
from control.services.capture_synthesis_service import suggest_key_fields


def _entity_clientes() -> EntityDefinition:
    return EntityDefinition(
        name="CLIENTES",
        storage_type="isam",
        fields=[
            FieldDefinition(name="CPF", datatype="cpf", unique_flag=True),
            FieldDefinition(name="NOME", datatype="person_name"),
            FieldDefinition(name="EMAIL", datatype="email"),
        ],
        indexes=[{"field": "CPF", "index": "CPF"}],
        operations=[
            OperationDefinition(operation_type="seek", entity_name="CLIENTES", fields=["CPF"]),
            OperationDefinition(operation_type="replace", entity_name="CLIENTES", fields=["NOME"]),
        ],
    )


class SuggestKeyFieldsTests(unittest.TestCase):
    def test_campo_unique_e_sugerido(self):
        mappings = [{
            "entity_name": "CLIENTES",
            "inputs": [{"original": "00109829069", "field_name": "cpf", "placeholder": "{{clientes.cpf}}"}],
        }]
        self.assertEqual(suggest_key_fields(mappings, [_entity_clientes()]), ["cpf"])

    def test_campo_de_indice_e_sugerido_mesmo_sem_unique(self):
        entity = EntityDefinition(
            name="PRODUTOS",
            fields=[FieldDefinition(name="EAN"), FieldDefinition(name="DESCRICAO")],
            indexes=[{"field": "EAN", "index": "EAN"}],
        )
        mappings = [{
            "entity_name": "PRODUTOS",
            "inputs": [{"original": "7909521971642", "field_name": "ean", "placeholder": "{{produtos.ean}}"}],
        }]
        self.assertEqual(suggest_key_fields(mappings, [entity]), ["ean"])

    def test_campo_de_operacao_seek_e_sugerido(self):
        entity = EntityDefinition(
            name="PEDIDOS",
            fields=[FieldDefinition(name="NUMERO"), FieldDefinition(name="VALOR")],
            operations=[OperationDefinition(operation_type="locate", entity_name="PEDIDOS", fields=["NUMERO"])],
        )
        mappings = [{
            "entity_name": "PEDIDOS",
            "inputs": [{"original": "D00011128", "field_name": "numero", "placeholder": "{{pedidos.numero}}"}],
        }]
        self.assertEqual(suggest_key_fields(mappings, [entity]), ["numero"])

    def test_campo_identificador_por_datatype_e_sugerido(self):
        """Tipo identificador de registro (cpf/cnpj/...) — âncora mesmo sem
        unique_flag (a KB persistida do AIX não popula unique nem índices)."""
        entity = EntityDefinition(
            name="arq",
            fields=[
                FieldDefinition(name="cpf", datatype="cpf", unique_flag=False),
                FieldDefinition(name="nome", datatype="person_name"),
            ],
        )
        mappings = [{
            "entity_name": "arq",
            "inputs": [{"original": "00109829069", "field_name": "cpf", "placeholder": "{{arq.cpf}}"}],
        }]
        self.assertEqual(suggest_key_fields(mappings, [entity]), ["cpf"])

    def test_campo_com_lookup_table_e_sugerido(self):
        """Campo com lookup_table é FK: o valor precisa existir na entidade
        referenciada — substituir por inexistente desvia o fluxo."""
        entity = EntityDefinition(
            name="PEDIDOS",
            fields=[
                FieldDefinition(name="EAN_PRODUTO", datatype="text", lookup_table="PRODUTOS"),
                FieldDefinition(name="QUANTIDADE", datatype="number"),
            ],
        )
        mappings = [{
            "entity_name": "PEDIDOS",
            "inputs": [
                {"original": "7909521971642", "field_name": "ean_produto", "placeholder": "{{pedidos.ean_produto}}"},
                {"original": "1", "field_name": "quantidade", "placeholder": "{{pedidos.quantidade}}"},
            ],
        }]
        self.assertEqual(suggest_key_fields(mappings, [entity]), ["ean_produto"])

    def test_identifies_record_modulo_central(self):
        """A propriedade 'identifica registro' é declarativa e centralizada."""
        from dakota_gateway.source_analyzer.semantic_types import identifies_record

        self.assertTrue(identifies_record("cpf"))
        self.assertTrue(identifies_record("CNPJ"))
        self.assertFalse(identifies_record("email"))
        self.assertFalse(identifies_record(""))
        self.assertFalse(identifies_record(None))

    def test_campo_comum_nao_e_sugerido(self):
        mappings = [{
            "entity_name": "CLIENTES",
            "inputs": [
                {"original": "JOAO", "field_name": "nome", "placeholder": "{{clientes.nome}}"},
                {"original": "229,90", "field_name": "valor", "placeholder": "{{clientes.valor}}"},
            ],
        }]
        self.assertEqual(suggest_key_fields(mappings, [_entity_clientes()]), [])

    def test_entidade_ausente_ou_sem_mapping_nao_quebra(self):
        self.assertEqual(suggest_key_fields([], [_entity_clientes()]), [])
        self.assertEqual(suggest_key_fields([{"entity_name": "INEXISTENTE", "inputs": [{"field_name": "cpf", "original": "1"}]}], [_entity_clientes()]), [])
        self.assertEqual(suggest_key_fields(None, None), [])

    def test_campo_indexado_e_ancora_mesmo_sem_entidade_na_kb(self):
        """Campo que compõe chave de i<TABELA>.00N é código consultado pelo
        ERP — mantido mesmo quando a entidade da tela não tem metadados na
        KB (células de grade est361/est366, captura 62: modelo g2511→n9580
        caía em "Codigo nao cadastrado")."""
        indexed = {"rede", "loja", "modelo", "combinacao", "tamanho",
                   "sequencia", "codigo", "data"}
        mappings = [
            {"entity_name": "est361", "inputs": [
                {"original": "g2511", "field_name": "modelo", "method": "by_grid_source"},
                {"original": "0000235", "field_name": "comb", "method": "by_grid_source"},
                {"original": "2", "field_name": "qtd", "method": "by_grid_source"},
            ]},
            {"entity_name": "est366", "inputs": [
                {"original": "15", "field_name": "codigo", "method": "by_grid_source"},
                {"original": "229,9", "field_name": "valor", "method": "by_grid_source"},
                {"original": "1", "field_name": "parcelas", "method": "by_grid_source"},
            ]},
        ]
        self.assertEqual(
            suggest_key_fields(mappings, [], indexed_fields=indexed),
            ["modelo", "comb", "codigo"])

    def test_prefixo_casa_nome_abreviado_da_grade(self):
        """A grade abrevia a coluna (comb←combinacao, tam←tamanho); prefixo
        com ≥3 caracteres casa. Nomes curtos demais não casam por prefixo."""
        from control.services.capture_synthesis_service import _matches_indexed

        indexed = {"combinacao", "tamanho", "modelo"}
        self.assertTrue(_matches_indexed("comb", indexed))
        self.assertTrue(_matches_indexed("tam", indexed))
        self.assertTrue(_matches_indexed("modelo", indexed))
        self.assertFalse(_matches_indexed("qtd", indexed))
        self.assertFalse(_matches_indexed("mo", indexed))  # <3 chars

    def test_sem_indexed_fields_mantem_comportamento_anterior(self):
        mappings = [{
            "entity_name": "est361",
            "inputs": [{"original": "g2511", "field_name": "modelo", "method": "by_grid_source"}],
        }]
        self.assertEqual(suggest_key_fields(mappings, []), [])

    def test_dedup_preserva_ordem_da_captura(self):
        mappings = [
            {"entity_name": "CLIENTES", "inputs": [{"original": "1", "field_name": "cpf", "placeholder": "{{clientes.cpf}}"}]},
            {"entity_name": "CLIENTES", "inputs": [{"original": "1", "field_name": "cpf", "placeholder": "{{clientes.cpf}}"}]},
        ]
        self.assertEqual(suggest_key_fields(mappings, [_entity_clientes()]), ["cpf"])

    def test_lookup_table_do_input_ancora_mesmo_sem_metadados_na_kb(self):
        """O lookup_table derivado do VALID do fonte (fValida) ancora o campo
        mesmo quando a entidade da KB não tem o campo com FK declarada —
        caso do cfop da captura 73: VALID exige valor em uni500 e o valor
        livre gerado (9445) caiu em "Codigo nao cadastrado" (run 52)."""
        entity = EntityDefinition(name="arq", fields=[])
        mappings = [{
            "entity_name": "arq",
            "inputs": [
                {"original": "5102", "field_name": "cfop",
                 "placeholder": "{{arq.cfop}}", "lookup_table": "uni500",
                 "method": "by_cursor_position"},
                {"original": "399,7", "field_name": "valor",
                 "placeholder": "{{arq.valor}}", "method": "by_cursor_position"},
            ],
        }]
        self.assertEqual(
            suggest_key_fields(mappings, [entity], lookup_covered=set()),
            ["cfop"])

    def test_lookup_table_do_input_coberta_por_valores_reais_nao_ancora(self):
        """FK coberta por valores reais (lookup_values) varia dentro do
        cadastro — não precisa de âncora."""
        entity = EntityDefinition(name="arq", fields=[])
        mappings = [{
            "entity_name": "arq",
            "inputs": [
                {"original": "5102", "field_name": "cfop",
                 "placeholder": "{{arq.cfop}}", "lookup_table": "uni500",
                 "method": "by_cursor_position"},
            ],
        }]
        self.assertEqual(
            suggest_key_fields(mappings, [entity], lookup_covered={"uni500"}),
            [])

    def test_codigo_numerico_longo_sem_cobertura_e_ancora(self):
        """Valor com cara de código de registro (EAN-13, CPF, CNPJ — 8 a 14
        dígitos puros) é chave de cadastro: sem lista de valores reais para
        sortear, mantém o original em vez de gerar um código inexistente —
        caso do EAN da captura 73 (mapeado como 'observacao' da fin310, sem
        lookup_table): 7036643879947 cairia em "Codigo nao cadastrado"."""
        mappings = [{
            "entity_name": "fin310",
            "inputs": [
                {"original": "7909667373669", "field_name": "observacao",
                 "placeholder": "{{fin310.observacao}}", "is_grid": True,
                 "method": "by_grid_source"},
                {"original": "399,7", "field_name": "valor",
                 "placeholder": "{{fin310.valor}}", "is_grid": True,
                 "method": "by_grid_source"},
                {"original": "15", "field_name": "formapag",
                 "placeholder": "{{fin310.formapag}}", "is_grid": True,
                 "method": "by_grid_source"},
                {"original": "1000", "field_name": "qtd",
                 "placeholder": "{{fin310.qtd}}", "is_grid": True,
                 "method": "by_grid_source"},
            ],
        }]
        self.assertEqual(
            suggest_key_fields(mappings, [], lookup_covered=set()),
            ["observacao"])

    def test_codigo_numerico_longo_coberto_por_campo_nao_ancora(self):
        """Código com valores reais observados para o MESMO campo
        (field:<nome> no harvest) varia dentro dos códigos reais."""
        mappings = [{
            "entity_name": "fin310",
            "inputs": [
                {"original": "7909667373669", "field_name": "observacao",
                 "placeholder": "{{fin310.observacao}}", "is_grid": True,
                 "method": "by_grid_source"},
            ],
        }]
        self.assertEqual(
            suggest_key_fields(
                mappings, [], lookup_covered={"field:observacao"}),
            [])

    def test_harvest_indexa_valores_por_nome_de_campo(self):
        """O harvest de valores reais passa a indexar também por
        field:<campo> — permite variar códigos (EAN) mesmo quando a tabela
        FK é desconhecida ou o campo foi mapeado para a entidade errada."""
        import json
        import tempfile
        from control.services.capture_synthesis_service import (
            _harvest_lookup_values)

        with tempfile.TemporaryDirectory() as tmp:
            report = {
                "screen_mappings": [{
                    "entity_name": "fin310",
                    "inputs": [
                        {"original": "7899295395174", "field_name": "observacao",
                         "lookup_table": "", "entity_name": "fin310"},
                        {"original": "{KEY:ENTER}", "field_name": "",
                         "lookup_table": "", "entity_name": ""},
                        {"original": "5102", "field_name": "cfop",
                         "lookup_table": "uni500", "entity_name": "arq"},
                    ],
                }],
            }
            out = Path(tmp) / "cap-x" / "synthetic" / "run-1"
            out.mkdir(parents=True)
            (out / "report.json").write_text(
                json.dumps(report), encoding="utf-8")
            values = _harvest_lookup_values(Path(tmp))
        self.assertIn("7899295395174", values.get("field:observacao", []))
        self.assertIn("5102", values.get("field:cfop", []))
        self.assertIn("5102", values.get("uni500", []))
        self.assertNotIn("{KEY:ENTER}", values.get("field:observacao", []))

    def test_lookup_key_cai_para_field_quando_sem_tabela(self):
        """Sem lookup_table (FK desconhecida), o dataset usa a lista de
        valores reais indexada por field:<campo> quando existe — é o que
        permite variar o EAN entre códigos de produto reais."""
        from dakota_gateway.synthetic.journey_synthesizer import (
            _lookup_key_for_input)

        lv = {"field:observacao": ["7899295395174"]}
        self.assertEqual(
            _lookup_key_for_input("", "observacao", lv), "field:observacao")
        self.assertEqual(_lookup_key_for_input("uni500", "cfop", lv), "uni500")
        self.assertIsNone(_lookup_key_for_input("", "valor", lv))
        self.assertIsNone(_lookup_key_for_input("", "", lv))
        self.assertIsNone(_lookup_key_for_input("  ", "  ", lv))

    def test_depara_nota_valor_real_observado_por_campo(self):
        """De→para registra a origem do valor quando ele veio da lista
        field:<campo> (valores reais observados em capturas anteriores)."""
        from control.services.capture_synthesis_service import (
            _build_depara_screens)

        mappings = [{
            "entity_name": "fin310",
            "inputs": [{
                "original": "7909667373669", "field_name": "observacao",
                "placeholder": "{{fin310.observacao}}", "is_grid": True,
                "method": "by_grid_source",
            }],
        }]
        screens = _build_depara_screens(
            mappings, {"fin310.observacao": "7899295395174"}, set(),
            lookup_counts={"field:observacao": 3})
        self.assertEqual(len(screens), 1)
        field = screens[0]["fields"][0]
        self.assertEqual(field["synthetic"], "7899295395174")
        self.assertIn("valor real observado em capturas", field["note"])
        self.assertIn("1 de 3", field["note"])

    def test_sugestao_alimenta_extract_substitutions_como_identidade(self):
        """Âncora detectada vira substituição identidade sem skip explícito."""
        from control.services.capture_synthesis_service import _extract_substitutions

        mappings = [{
            "entity_name": "CLIENTES",
            "inputs": [
                {"original": "00109829069", "field_name": "cpf", "placeholder": "{{clientes.cpf}}"},
                {"original": "JOAO", "field_name": "nome", "placeholder": "{{clientes.nome}}"},
            ],
        }]
        keys = suggest_key_fields(mappings, [_entity_clientes()])
        subs = _extract_substitutions(
            mappings, {"cpf": "185.032.574-08", "nome": "MARIA"}, skip_fields=set(keys))
        self.assertEqual(subs[0], ("00109829069", "00109829069"))  # âncora mantida
        self.assertEqual(subs[1], ("JOAO", "MARIA"))  # dado comum substituído


if __name__ == "__main__":
    unittest.main()
