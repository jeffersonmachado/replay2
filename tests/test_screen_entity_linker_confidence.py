#!/usr/bin/env python3
"""Testes de confianca do ScreenEntityLinker com regras rigorosas."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.entity_catalog import (
    EntityDefinition, FieldDefinition, ScreenDefinition,
)
from dakota_gateway.source_analyzer.screen_entity_linker import ScreenEntityLinker


class ScreenEntityLinkerConfidenceTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.sd = Path(self.tmpdir.name)
        self.linker = ScreenEntityLinker(str(self.sd))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _w(self, name: str, content: str) -> Path:
        p = self.sd / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_single_generic_field_low_confidence(self):
        """Um unico campo generico (NOME) nao pode gerar confidence >= 0.75."""
        screen = ScreenDefinition(
            title="Tela Generica", program_name="x",
            source_file=str(self.sd / "x.prg"),
            fields=[FieldDefinition(name="nome", prompt="Nome:")],
        )
        entities = [EntityDefinition(
            name="CLIENTES", storage_type="sql",
            fields=[FieldDefinition(name="NOME"), FieldDefinition(name="ID")],
        )]
        bindings = self.linker.link([screen], entities)
        self.assertEqual(len(bindings), 1)
        self.assertLess(bindings[0].confidence, 0.75,
                        "Campo generico sozinho nao deve ter confianca alta")

    def test_cadcli_alias_matches_clientes(self):
        """cadcli deve associar com CLIENTES por alias."""
        self._w("cadcli.prg", """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:" GET nome
@ 2,1 SAY "CPF:" GET cpf
@ 3,1 SAY "Email:" GET email
INSERT INTO CLIENTES (NOME, CPF, EMAIL) VALUES (nome, cpf, email)
""")
        screen = ScreenDefinition(
            title="Cadastro de Clientes", program_name="cadcli",
            source_file=str(self.sd / "cadcli.prg"),
            source_lines=(2, 4),
            fields=[
                FieldDefinition(name="nome", prompt="Nome:"),
                FieldDefinition(name="cpf", prompt="CPF:"),
                FieldDefinition(name="email", prompt="Email:"),
            ],
        )
        entities = [EntityDefinition(
            name="CLIENTES", storage_type="sql",
            fields=[
                FieldDefinition(name="NOME"), FieldDefinition(name="CPF"),
                FieldDefinition(name="EMAIL"), FieldDefinition(name="TELEFONE"),
            ],
        )]
        bindings = self.linker.link([screen], entities)
        self.assertEqual(bindings[0].entity_name, "CLIENTES")
        self.assertGreaterEqual(bindings[0].confidence, 0.65,
                                "cadcli + titulo devem gerar confianca >= media-alta")

    def test_altprod_alias_matches_produtos(self):
        """altprod deve associar com PRODUTOS por alias."""
        screen = ScreenDefinition(
            title="Alteracao de Produtos", program_name="altprod",
            source_file=str(self.sd / "altprod.prg"),
            source_lines=(2, 4),
            fields=[
                FieldDefinition(name="descricao", prompt="Descricao:"),
                FieldDefinition(name="preco", prompt="Preco:"),
            ],
        )
        entities = [
            EntityDefinition(name="PRODUTOS", storage_type="isam",
                fields=[FieldDefinition(name="DESCRICAO"), FieldDefinition(name="PRECO")]),
        ]
        bindings = self.linker.link([screen], entities)
        self.assertEqual(bindings[0].entity_name, "PRODUTOS")
        # Alias + titulo devem gerar confianca >= media
        self.assertGreaterEqual(bindings[0].confidence, 0.35)

    def test_titulo_campo_use_alta_confianca(self):
        """Titulo 'Cadastro' + campos especificos + USE deve gerar confianca alta."""
        self._w("cadcli.prg", """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "CPF:" GET cpf
@ 2,1 SAY "Email:" GET email
USE CLIENTES
APPEND BLANK
""")
        screen = ScreenDefinition(
            title="Cadastro de Clientes", program_name="cadcli",
            source_file=str(self.sd / "cadcli.prg"),
            source_lines=(2, 3),
            fields=[
                FieldDefinition(name="cpf", prompt="CPF:"),
                FieldDefinition(name="email", prompt="Email:"),
            ],
        )
        entities = [EntityDefinition(
            name="CLIENTES", storage_type="isam",
            fields=[FieldDefinition(name="CPF"), FieldDefinition(name="EMAIL")],
        )]
        bindings = self.linker.link([screen], entities)
        self.assertEqual(bindings[0].entity_name, "CLIENTES")
        self.assertEqual(bindings[0].operation, "create")
        # Campos especificos como CPF, EMAIL (nao genericos) + alias cadcli
        self.assertGreaterEqual(bindings[0].confidence, 0.65,
                                "Titulo + campos (CPF/EMAIL) + USE/APPEND = media-alta")

    def test_operacao_inferida_pelo_trecho_correto(self):
        """Operacao deve ser inferida pelo trecho source_lines, nao arquivo inteiro."""
        self._w("multitelas.prg", """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:" GET nome
INSERT INTO CLIENTES (NOME) VALUES (nome)

TITLE "Consulta de Clientes"
@ 1,1 SAY "CPF:" GET cpf
SELECT * FROM CLIENTES WHERE CPF = cpf
""")
        # Tela 1: Cadastro
        s1 = ScreenDefinition(
            title="Cadastro de Clientes", program_name="multitelas",
            source_file=str(self.sd / "multitelas.prg"),
            source_lines=(2, 3),
            fields=[FieldDefinition(name="nome", prompt="Nome:")],
        )
        # Tela 2: Consulta
        s2 = ScreenDefinition(
            title="Consulta de Clientes", program_name="multitelas",
            source_file=str(self.sd / "multitelas.prg"),
            source_lines=(6, 7),
            fields=[FieldDefinition(name="cpf", prompt="CPF:")],
        )
        entities = [EntityDefinition(
            name="CLIENTES", storage_type="sql",
            fields=[FieldDefinition(name="NOME"), FieldDefinition(name="CPF")],
        )]
        bindings = self.linker.link([s1, s2], entities)
        self.assertEqual(bindings[0].operation, "create")
        self.assertEqual(bindings[1].operation, "read")

    # ── v0.2.1 ──

    def test_cpf_email_nao_sao_genericos_fracos(self):
        """CPF e EMAIL devem contribuir como campos fortes."""
        screen = ScreenDefinition(
            title="Cadastro de Clientes", program_name="cadcli",
            source_file=str(self.sd / "cadcli.prg"),
            source_lines=(2, 4),
            fields=[
                FieldDefinition(name="nome", prompt="Nome:"),
                FieldDefinition(name="cpf", prompt="CPF:"),
                FieldDefinition(name="email", prompt="Email:"),
            ],
        )
        entities = [EntityDefinition(
            name="CLIENTES", storage_type="sql",
            fields=[
                FieldDefinition(name="NOME"), FieldDefinition(name="CPF"),
                FieldDefinition(name="EMAIL"),
            ],
        )]
        bindings = self.linker.link([screen], entities)
        self.assertEqual(bindings[0].entity_name, "CLIENTES")
        self.assertGreaterEqual(bindings[0].confidence, 0.65,
                                "CPF+EMAIL devem gerar confianca media-alta")

    def test_nome_sozinho_baixa_confianca(self):
        """NOME sozinho nao deve gerar confianca alta."""
        screen = ScreenDefinition(
            title="Tela", program_name="x",
            source_file=str(self.sd / "x.prg"),
            fields=[FieldDefinition(name="nome", prompt="Nome:")],
        )
        entities = [
            EntityDefinition(name="CLIENTES", storage_type="sql",
                fields=[FieldDefinition(name="NOME"), FieldDefinition(name="ID")]),
            EntityDefinition(name="FORNECEDORES", storage_type="sql",
                fields=[FieldDefinition(name="NOME"), FieldDefinition(name="ID")]),
        ]
        bindings = self.linker.link([screen], entities)
        self.assertLess(bindings[0].confidence, 0.75,
                        "NOME sozinho nao deve gerar confianca alta")

    # ── v0.2.2: matched_fields preserva ordem da tela ──

    def test_matched_fields_preserves_screen_order(self):
        """matched_fields deve preservar a ordem dos campos na tela, nao ordem alfabetica."""
        screen = ScreenDefinition(
            title="Cadastro de Clientes",
            program_name="cadcli",
            source_file=str(self.sd / "cadcli.prg"),
            fields=[
                FieldDefinition(name="cpf", prompt="CPF:"),
                FieldDefinition(name="nome", prompt="Nome:"),
                FieldDefinition(name="endereco", prompt="Endereco:"),
            ],
        )
        entities = [
            EntityDefinition(name="CLIENTES", storage_type="sql",
                fields=[
                    FieldDefinition(name="ID"),
                    FieldDefinition(name="CPF"),
                    FieldDefinition(name="NOME"),
                    FieldDefinition(name="ENDERECO"),
                ]),
        ]
        bindings = self.linker.link([screen], entities)
        self.assertEqual(len(bindings), 1)
        b = bindings[0]

        # Ordem deve ser: CPF, NOME, ENDERECO (ordem da tela)
        # Nao ordem alfabetica: CPF, ENDERECO, NOME
        matched_upper = [f.upper() for f in b.matched_fields]
        self.assertEqual(matched_upper, ["CPF", "NOME", "ENDERECO"],
                         f"Ordem incorreta: {b.matched_fields}")

    # ── v0.2.2: Abreviacoes Dakota ──

    def test_desc_alias_matches_descricao_preserving_screen_order(self):
        """cDesc/desc casa com DESCRICAO, nValor/valor casa com VALOR."""
        screen = ScreenDefinition(
            title="Cadastro de Produtos",
            program_name="cadprod",
            source_file=str(self.sd / "cadprod.prg"),
            fields=[
                FieldDefinition(name="desc", prompt="Descricao:"),
                FieldDefinition(name="valor", prompt="Valor:"),
            ],
        )
        entities = [
            EntityDefinition(name="PRODUTOS", storage_type="sql",
                fields=[
                    FieldDefinition(name="ID"),
                    FieldDefinition(name="DESCRICAO"),
                    FieldDefinition(name="VALOR"),
                ]),
        ]
        bindings = self.linker.link([screen], entities)
        self.assertEqual(len(bindings), 1)
        b = bindings[0]

        matched_upper = [f.upper() for f in b.matched_fields]
        self.assertEqual(matched_upper, ["DESCRICAO", "VALOR"],
                         f"matched_fields deve conter DESCRICAO e VALOR: {b.matched_fields}")

    # ── v0.2.3: Raw Hungarian + compound abbreviations ──

    def test_raw_hungarian_desc_fields_match_descricao(self):
        """cDesc/cDescr/cDescricao crus casam com DESCRICAO sem ScreenExtractor."""
        screen = ScreenDefinition(
            title="Cadastro de Produtos",
            program_name="cadprod",
            source_file=str(self.sd / "cadprod.prg"),
            fields=[
                FieldDefinition(name="cDesc", prompt="Descricao:"),
                FieldDefinition(name="nValor", prompt="Valor:"),
            ],
        )
        entities = [
            EntityDefinition(name="PRODUTOS", storage_type="sql",
                fields=[
                    FieldDefinition(name="ID"),
                    FieldDefinition(name="DESCRICAO"),
                    FieldDefinition(name="VALOR"),
                ]),
        ]
        bindings = self.linker.link([screen], entities)
        self.assertEqual(len(bindings), 1)
        b = bindings[0]
        matched_upper = [f.upper() for f in b.matched_fields]
        self.assertEqual(matched_upper, ["DESCRICAO", "VALOR"],
                         f"Raw hungarian: {b.matched_fields}")

    def test_raw_hungarian_multiple_variants(self):
        """Multiplas variantes hungaras: cDesc, cDescr, nVlr, nPreco."""
        cases = [
            (["cDesc", "nValor"], ["DESCRICAO", "VALOR"]),
            (["cDescr", "nValor"], ["DESCRICAO", "VALOR"]),
            (["cDescricao", "nValor"], ["DESCRICAO", "VALOR"]),
            (["cDesc", "nVlr"], ["DESCRICAO", "VALOR"]),
            (["cDescr", "nPreco"], ["DESCRICAO", "PRECO"]),
        ]
        for screen_fields, expected in cases:
            screen = ScreenDefinition(
                title="Cadastro de Produtos",
                program_name="cadprod",
                source_file=str(self.sd / "cadprod.prg"),
                fields=[FieldDefinition(name=f) for f in screen_fields],
            )
            entities = [
                EntityDefinition(name="PRODUTOS", storage_type="sql",
                    fields=[FieldDefinition(name="ID"),
                            FieldDefinition(name="DESCRICAO"),
                            FieldDefinition(name="VALOR"),
                            FieldDefinition(name="PRECO")]),
            ]
            bindings = self.linker.link([screen], entities)
            b = bindings[0]
            matched_upper = [f.upper() for f in b.matched_fields]
            self.assertEqual(matched_upper, expected,
                             f"fields={screen_fields}: got {b.matched_fields}")

    def test_compound_description_aliases_match_descricao(self):
        """descr_prod, des_prod, desc_prod, descricao_prod casam com DESCRICAO."""
        cases = [
            (["descr_prod", "vlr_prod"], ["DESCRICAO", "VALOR"]),
            (["des_prod", "vlr_prod"], ["DESCRICAO", "VALOR"]),
            (["desc_prod", "vlr_prod"], ["DESCRICAO", "VALOR"]),
            (["descricao_prod", "vlr_prod"], ["DESCRICAO", "VALOR"]),
            (["descricao_produto", "vlr_prod"], ["DESCRICAO", "VALOR"]),
            (["desc_prod", "preco_prod"], ["DESCRICAO", "PRECO"]),
            (["descrproduto", "vlrprod"], ["DESCRICAO", "VALOR"]),
            (["descproduto", "valorprod"], ["DESCRICAO", "VALOR"]),
        ]
        for screen_fields, expected in cases:
            screen = ScreenDefinition(
                title="Cadastro de Produtos",
                program_name="cadprod",
                source_file=str(self.sd / "cadprod.prg"),
                fields=[FieldDefinition(name=f) for f in screen_fields],
            )
            entities = [
                EntityDefinition(name="PRODUTOS", storage_type="sql",
                    fields=[FieldDefinition(name="ID"),
                            FieldDefinition(name="DESCRICAO"),
                            FieldDefinition(name="VALOR"),
                            FieldDefinition(name="PRECO")]),
            ]
            bindings = self.linker.link([screen], entities)
            b = bindings[0]
            matched_upper = [f.upper() for f in b.matched_fields]
            self.assertEqual(matched_upper, expected,
                             f"fields={screen_fields}: got {b.matched_fields}")

    # ── v0.2.4: Accented Hungarian ──

    def test_accented_hungarian_description_matches_descricao(self):
        """cDescrição/cDescriçao/cDescricão casam com DESCRICAO."""
        cases = [
            (["cDescrição", "nValor"], ["DESCRICAO", "VALOR"]),
            (["cDescriçao", "nValor"], ["DESCRICAO", "VALOR"]),
            (["cDescricão", "nValor"], ["DESCRICAO", "VALOR"]),
            (["cDescrição", "nPreco"], ["DESCRICAO", "PRECO"]),
        ]
        for screen_fields, expected in cases:
            screen = ScreenDefinition(
                title="Cadastro de Produtos",
                program_name="cadprod",
                source_file=str(self.sd / "cadprod.prg"),
                fields=[FieldDefinition(name=f) for f in screen_fields],
            )
            entities = [
                EntityDefinition(name="PRODUTOS", storage_type="sql",
                    fields=[FieldDefinition(name="ID"),
                            FieldDefinition(name="DESCRICAO"),
                            FieldDefinition(name="VALOR"),
                            FieldDefinition(name="PRECO")]),
            ]
            bindings = self.linker.link([screen], entities)
            b = bindings[0]
            matched_upper = [f.upper() for f in b.matched_fields]
            self.assertEqual(matched_upper, expected,
                             f"accented fields={screen_fields}: got {b.matched_fields}")


if __name__ == "__main__":
    unittest.main()
