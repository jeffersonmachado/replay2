"""Testes do leitor de arquivos de índice Recital (i<TABELA>.00N)."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dakota_gateway.source_analyzer.index_file_reader import (
    discover_data_dir,
    enrich_entities_with_index_files,
    parse_key_expression,
    read_index_expression,
    scan_index_files,
)
from dakota_gateway.source_analyzer.entity_catalog import EntityDefinition


def _write_index(path: Path, expression: str) -> None:
    """Arquivo de índice sintético: expressão ASCII + padding NUL (512)."""
    path.write_bytes(expression.encode("ascii") + b"\x00" * (512 - len(expression)))


class ParseKeyExpressionTests(unittest.TestCase):
    def test_chave_simples(self):
        self.assertEqual(parse_key_expression("codigo"), ["codigo"])

    def test_chave_composta(self):
        self.assertEqual(parse_key_expression("rede + loja"), ["rede", "loja"])

    def test_funcao_e_desembrulhada(self):
        self.assertEqual(parse_key_expression("rede + loja + dtos(data)"), ["rede", "loja", "data"])
        self.assertEqual(parse_key_expression("upper(nome)"), ["nome"])

    def test_lixo_nao_vira_campo(self):
        self.assertEqual(parse_key_expression(""), [])
        self.assertEqual(parse_key_expression("123 + "), [])


class ScanIndexFilesTests(unittest.TestCase):
    def test_varre_indices_com_tabela_par(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "est100.est").write_bytes(b"\x00" * 64)
            _write_index(base / "iest100.001", "rede + loja")
            _write_index(base / "iest100.002", "codigo")
            keys = scan_index_files(base)
        self.assertEqual(keys, {"EST100": [["rede", "loja"], ["codigo"]]})

    def test_indice_sem_tabela_par_e_ignorado(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_index(base / "iest999.001", "rede + loja")
            self.assertEqual(scan_index_files(base), {})

    def test_diretorio_inexistente(self):
        self.assertEqual(scan_index_files("/nao/existe"), {})


class DiscoverDataDirTests(unittest.TestCase):
    def test_env_tem_precedencia(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(
                discover_data_dir("/qualquer", env={"DAKOTA_DATA_ROOT": tmp}), tmp
            )

    def test_irmao_com_indices_e_descoberto(self):
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            (parent / "prg").mkdir()
            est = parent / "est"
            est.mkdir()
            (est / "est100.est").write_bytes(b"\x00" * 64)
            _write_index(est / "iest100.001", "codigo")
            self.assertEqual(
                discover_data_dir(parent / "prg", env={}), str(est)
            )

    def test_sem_indice_retorna_vazio(self):
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            (parent / "prg").mkdir()
            (parent / "vazio").mkdir()
            self.assertEqual(discover_data_dir(parent / "prg", env={}), "")


class EnrichEntitiesTests(unittest.TestCase):
    def test_campos_da_chave_viram_indices_da_entidade(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "est100.est").write_bytes(b"\x00" * 64)
            _write_index(base / "iest100.001", "rede + loja + dtos(data)")
            entity = EntityDefinition(name="EST100", fields=[])
            enriched = enrich_entities_with_index_files([entity], base)
            campos = {idx["field"] for idx in entity.indexes}
        self.assertEqual(enriched, 1)
        self.assertEqual(campos, {"rede", "loja", "data"})

    def test_entidade_sem_arquivo_correspondente_nao_muda(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "est100.est").write_bytes(b"\x00" * 64)
            _write_index(base / "iest100.001", "codigo")
            entity = EntityDefinition(name="ARQ360", fields=[])
            enriched = enrich_entities_with_index_files([entity], base)
        self.assertEqual(enriched, 0)
        self.assertEqual(entity.indexes, [])

    def test_idempotente(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "est100.est").write_bytes(b"\x00" * 64)
            _write_index(base / "iest100.001", "codigo")
            entity = EntityDefinition(name="EST100", fields=[])
            enrich_entities_with_index_files([entity], base)
            enrich_entities_with_index_files([entity], base)
        self.assertEqual(len(entity.indexes), 1)


class IntegracaoSuggestKeyFieldsTests(unittest.TestCase):
    def test_campo_de_indice_de_arquivo_e_ancora(self):
        """Índice vindo do arquivo alimenta a regra (a) do suggest_key_fields."""
        from control.services.capture_synthesis_service import suggest_key_fields

        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "est100.est").write_bytes(b"\x00" * 64)
            _write_index(base / "iest100.001", "rede + loja")
            entity = EntityDefinition(name="EST100", fields=[])
            enrich_entities_with_index_files([entity], base)
            mappings = [{
                "entity_name": "EST100",
                "inputs": [
                    {"original": "01", "field_name": "rede", "placeholder": "{{est100.rede}}"},
                    {"original": "x", "field_name": "obs", "placeholder": "{{est100.obs}}"},
                ],
            }]
            self.assertEqual(suggest_key_fields(mappings, [entity]), ["rede"])


if __name__ == "__main__":
    unittest.main()
