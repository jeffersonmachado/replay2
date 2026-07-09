#!/usr/bin/env python3
"""Testes do ScreenExtractor com padroes reais de codigo-fonte Dakota."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.source_analyzer.screen_extractor import ScreenExtractor


class ScreenExtractorRealPatternsTests(unittest.TestCase):

    def test_title_before_screen(self):
        """TITLE antes do primeiro @ SAY/GET deve ser capturado."""
        content = """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
@ 2,1 SAY "CPF:"
@ 2,20 GET cpf
"""
        screens = ScreenExtractor.extract(content, "cadcli.prg")
        self.assertEqual(len(screens), 1)
        self.assertEqual(screens[0].title, "Cadastro de Clientes")
        self.assertEqual(len(screens[0].fields), 2)

    def test_say_get_inline(self):
        """SAY "Label:" GET campo na mesma linha."""
        content = """
@ 1,1 SAY "Nome:" GET nome
@ 2,1 SAY "CPF:" GET cpf
"""
        screens = ScreenExtractor.extract(content, "test.prg")
        self.assertEqual(len(screens), 1)
        self.assertEqual(len(screens[0].fields), 2)
        self.assertEqual(screens[0].fields[0].name, "nome")
        self.assertEqual(screens[0].fields[0].prompt, "Nome:")
        self.assertEqual(screens[0].fields[0].row, 1)
        self.assertEqual(screens[0].fields[0].col, 1)

    def test_get_m_campo(self):
        """GET m.campo deve ser normalizado para campo."""
        content = """
@ 1,1 SAY "Nome:" GET m.nome
@ 2,1 SAY "Valor:" GET n.valor
@ 3,1 SAY "Desc:" GET c.descricao
"""
        screens = ScreenExtractor.extract(content, "test.prg")
        self.assertEqual(len(screens), 1)
        field_names = {f.name for f in screens[0].fields}
        self.assertIn("nome", field_names)
        self.assertIn("valor", field_names)
        self.assertIn("descricao", field_names)
        # m., n., c. nao devem aparecer no nome
        self.assertNotIn("m.nome", field_names)

    def test_picture_valid_when(self):
        """PICTURE, VALID e WHEN devem ser extraidos."""
        content = """
@ 1,1 SAY "CPF:" GET cpf
PICTURE "999.999.999-99"
VALID !EMPTY(cpf)
@ 2,1 SAY "Data:" GET data_nasc
PICTURE "@D"
WHEN data_nasc > CTOD("01/01/1900")
"""
        screens = ScreenExtractor.extract(content, "test.prg")
        self.assertEqual(len(screens), 1)
        cpf_field = screens[0].fields[0]
        self.assertEqual(cpf_field.picture, "999.999.999-99")
        self.assertIn("EMPTY", cpf_field.valid_expr or "")

        data_field = screens[0].fields[1]
        self.assertEqual(data_field.picture, "@D")
        self.assertIn("1900", data_field.when_expr or "")

    def test_program_name_from_file(self):
        """Se nao houver PROCEDURE/FUNCTION/PROGRAM, usar nome do arquivo."""
        content = """
TITLE "Tela sem procedure"
@ 1,1 SAY "Campo:" GET campo
"""
        screens = ScreenExtractor.extract(content, "/dakota/prg/cad/cadcli.prg")
        self.assertEqual(len(screens), 1)
        self.assertEqual(screens[0].program_name, "cadcli")

    def test_menu_numerado_sem_get(self):
        """Menu numerado com SAY "1 - Opcao" sem GET."""
        content = """
TITLE "Menu Principal"
@ 1,1 SAY "1 - Cadastros"
@ 2,1 SAY "2 - Vendas"
@ 3,1 SAY "3 - Relatorios"
@ 4,1 SAY "0 - Sair"
"""
        screens = ScreenExtractor.extract(content, "menu.prg")
        self.assertEqual(len(screens), 1)
        self.assertEqual(screens[0].title, "Menu Principal")
        # Menu sem GET — 0 campos
        self.assertEqual(len(screens[0].fields), 0)

    def test_title_titulo_caption_variants(self):
        """TITLE, TITULO e CAPTION devem ser reconhecidos."""
        for keyword in ("TITLE", "TITULO", "CAPTION"):
            content = f"""
{keyword} "Tela de Teste"
@ 1,1 SAY "X:" GET x
"""
            screens = ScreenExtractor.extract(content, "t.prg")
            self.assertEqual(screens[0].title, "Tela de Teste",
                             f"Falhou para {keyword}")

    def test_source_lines_preserved(self):
        """source_lines deve refletir o intervalo real da tela."""
        content = """
TITLE "Cadastro"
@ 1,1 SAY "Nome:" GET nome
@ 2,1 SAY "CPF:" GET cpf
USE CLIENTES
APPEND BLANK
"""
        screens = ScreenExtractor.extract(content, "cad.prg")
        self.assertEqual(len(screens), 1)
        start, end = screens[0].source_lines
        self.assertGreaterEqual(start, 1)
        self.assertGreater(end, start)

    # ── v0.2.1: Multiplas telas ──

    def test_multiple_screens_separated_by_read(self):
        """Duas telas separadas por READ."""
        content = '''
TITLE "Cadastro Cliente"
@ 1,1 SAY "CPF:" GET cpf
READ

TITLE "Cadastro Produto"
@ 1,1 SAY "Descricao:" GET descricao
READ
'''
        screens = ScreenExtractor.extract(content, "multi.prg")
        self.assertEqual(len(screens), 2, f"Esperado 2 telas, obtido {len(screens)}")
        self.assertIn("Cliente", screens[0].title)
        self.assertTrue(any(f.name == "cpf" for f in screens[0].fields))
        self.assertIn("Produto", screens[1].title)
        self.assertTrue(any(f.name == "descricao" for f in screens[1].fields))

    def test_picture_valid_when_inline_on_get(self):
        """PICTURE/VALID/WHEN inline no GET."""
        content = '@ 1,1 SAY "CPF:" GET m.cpf PICTURE "999.999.999-99" VALID ValCPF(m.cpf) WHEN lEdita'
        screens = ScreenExtractor.extract(content, "t.prg")
        self.assertEqual(len(screens), 1)
        field = screens[0].fields[0]
        self.assertEqual(field.name, "cpf")
        self.assertEqual(field.picture, "999.999.999-99")
        self.assertIn("ValCPF", field.valid_expr or "")
        self.assertIn("lEdita", field.when_expr or "")

    def test_hungarian_notation_normalization(self):
        """cNome→nome, nValor→valor, dData→data, lAtivo→ativo."""
        content = """
@ 1,1 SAY "Nome:" GET cNome
@ 2,1 SAY "Valor:" GET nValor
@ 3,1 SAY "Data:" GET dData
@ 4,1 SAY "Ativo:" GET lAtivo
"""
        screens = ScreenExtractor.extract(content, "t.prg")
        self.assertEqual(len(screens), 1)
        names = {f.name for f in screens[0].fields}
        self.assertIn("nome", names)
        self.assertIn("valor", names)
        self.assertIn("data", names)
        self.assertIn("ativo", names)

    # ── v0.2.1: GET standalone VALID/WHEN + source_lines TITLE ──

    def test_get_standalone_valid_when_full(self):
        """GET standalone com PICTURE VALID WHEN deve capturar tudo."""
        content = '@ 1,1 GET m.cpf PICTURE "999.999.999-99" VALID ValCPF(m.cpf) WHEN lEdita'
        screens = ScreenExtractor.extract(content, "t.prg")
        self.assertEqual(len(screens), 1)
        f = screens[0].fields[0]
        self.assertEqual(f.name, "cpf")
        self.assertEqual(f.picture, "999.999.999-99")
        self.assertIn("ValCPF", f.valid_expr or "")
        self.assertIn("lEdita", f.when_expr or "")

    def test_get_standalone_valid_only(self):
        """GET standalone apenas com VALID."""
        content = '@ 1,1 GET cNome PICTURE "@!" VALID !EMPTY(cNome)'
        screens = ScreenExtractor.extract(content, "t.prg")
        f = screens[0].fields[0]
        self.assertEqual(f.name, "nome")
        self.assertEqual(f.picture, "@!")
        self.assertIn("EMPTY", f.valid_expr or "")

    def test_source_lines_includes_title(self):
        """source_lines deve incluir a linha do TITLE."""
        content = """
TITLE "Cadastro Cliente"
@ 1,1 SAY "CPF:" GET cpf
READ
"""
        screens = ScreenExtractor.extract(content, "t.prg")
        self.assertEqual(len(screens), 1)
        start, end = screens[0].source_lines
        self.assertEqual(start, 2, f"source_lines deve comecar no TITLE (linha 2), mas comecou em {start}")
        self.assertGreaterEqual(end, 3)


if __name__ == "__main__":
    unittest.main()
