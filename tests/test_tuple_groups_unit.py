#!/usr/bin/env python3
"""Testes da variação em par (tupla de chave composta) da síntese.

Campos que consultam um cadastro EM CONJUNTO (ex.: modelo+combinação do
produto na grade da OC) são âncora pela passada de índice. Com as tuplas
reais amostradas da tabela referenciada, o grupo inteiro pode variar —
desde que todos os campos do par saiam do MESMO registro do cadastro.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))
sys.path.insert(0, str(GATEWAY_DIR / "control"))
sys.path.insert(0, str(ROOT / "tests"))  # fixtures compartilhadas de teste

from control.services.capture_synthesis_service import (
    _expand_skip_with_groups,
    find_tuple_groups,
    suggest_key_fields,
)
from dakota_gateway.synthetic.dataset_builder import DatasetBuilder
from dakota_gateway.synthetic.schema import FieldSchema, ScreenSchema, SyntheticSchema

from test_table_file_reader_unit import _write_index, _write_table


class _Inp:
    def __init__(self, field_name, entity_name="", method="by_grid_source"):
        self.field_name = field_name
        self.entity_name = entity_name
        self.method = method


class _Step:
    def __init__(self, entity_name, inputs):
        self.entity_name = entity_name
        self.inputs = inputs


class _Template:
    def __init__(self, steps):
        self.steps = steps


def _setup_produtos(base: Path) -> None:
    """Cadastro de variantes de produto: chave composta modelo+combinacao."""
    _write_table(
        base / "arq2i3.cad",
        [("MODELO", "C", 4), ("COMBINACAO", "C", 3), ("DESC", "C", 10)],
        [
            {"MODELO": "g251", "COMBINACAO": "001"},
            {"MODELO": "g252", "COMBINACAO": "002"},
            {"MODELO": "h100", "COMBINACAO": "001"},
        ],
    )
    # No legado o par do índice é o objeto compilado homônimo
    # (``<mod><NNN>.dbo``) — o dado mora no ``arq<NNN>.<mod>``.
    (base / "cad2i3.dbo").write_bytes(b"duz(p_idiom) objeto compilado")
    _write_index(base / "icad2i3.001", "modelo + combinacao")


class FindTupleGroupsTests(unittest.TestCase):
    def test_detecta_par_por_chave_composta(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "cad"
            base.mkdir()
            _setup_produtos(base)
            template = _Template([
                _Step("CMP311", [
                    _Inp("modelo", entity_name="CAD2D0"),
                    _Inp("comb", entity_name="CAD2D0"),
                    _Inp("qtd", entity_name="CAD2D0"),
                ]),
            ])
            groups = find_tuple_groups(template, [str(base)])
        self.assertIn("CAD2D0", groups)
        ent = groups["CAD2D0"]
        self.assertEqual(set(ent["fields_map"]), {"modelo", "comb"})
        group = next(iter(ent["groups"].values()))
        self.assertEqual(group["fields"], ["modelo", "comb"])
        self.assertIn(("g251", "001"), group["tuples"])
        self.assertIn(("h100", "001"), group["tuples"])

    def test_campo_sozinho_nao_forma_grupo(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "cad"
            base.mkdir()
            _setup_produtos(base)
            template = _Template([
                _Step("CMP310", [_Inp("modelo", entity_name="CMP310")]),
            ])
            self.assertEqual(find_tuple_groups(template, [str(base)]), {})

    def test_sem_tabela_parseavel_nao_ha_grupo(self):
        with TemporaryDirectory() as tmp:
            template = _Template([
                _Step("CMP311", [
                    _Inp("modelo", entity_name="CAD2D0"),
                    _Inp("comb", entity_name="CAD2D0"),
                ]),
            ])
            self.assertEqual(find_tuple_groups(template, [tmp]), {})

    def test_inputs_command_e_unmapped_nao_entram(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "cad"
            base.mkdir()
            _setup_produtos(base)
            template = _Template([
                _Step("CMP311", [
                    _Inp("modelo", entity_name="CAD2D0", method="command"),
                    _Inp("comb", entity_name="CAD2D0", method="unmapped"),
                ]),
            ])
            self.assertEqual(find_tuple_groups(template, [str(base)]), {})


class TupleCoveredAnchorTests(unittest.TestCase):
    """Campo ancorado só pela passada de índice é liberado quando coberto
    por tupla real; os demais continuam âncora."""

    def _mappings(self):
        return [{
            "entity_name": "CAD2D0",
            "inputs": [
                {"field_name": "modelo", "original": "g251"},
                {"field_name": "comb", "original": "001"},
                {"field_name": "qtd", "original": "1"},
            ],
        }]

    def test_par_coberto_por_tupla_deixa_de_ser_ancora(self):
        keys = suggest_key_fields(
            self._mappings(), [],
            indexed_fields={"modelo", "combinacao", "qtd"},
            tuple_covered={"modelo", "comb"},
        )
        self.assertNotIn("modelo", keys)
        self.assertNotIn("comb", keys)
        self.assertIn("qtd", keys)  # sem tupla: segue âncora

    def test_sem_cobertura_par_segue_ancora(self):
        keys = suggest_key_fields(
            self._mappings(), [],
            indexed_fields={"modelo", "combinacao"},
        )
        self.assertEqual(keys, ["modelo", "comb"])


class ExpandSkipWithGroupsTests(unittest.TestCase):
    def test_skip_parcial_expande_para_o_grupo_inteiro(self):
        groups = {
            "CAD2D0": {
                "fields_map": {"modelo": ("g1", 0), "comb": ("g1", 1)},
                "groups": {"g1": {"fields": ["modelo", "comb"], "tuples": []}},
            }
        }
        self.assertEqual(
            sorted(_expand_skip_with_groups(["comb"], groups)),
            ["comb", "modelo"],
        )

    def test_skip_fora_de_grupo_nao_muda(self):
        groups = {
            "CAD2D0": {
                "fields_map": {"modelo": ("g1", 0), "comb": ("g1", 1)},
                "groups": {"g1": {"fields": ["modelo", "comb"], "tuples": []}},
            }
        }
        self.assertEqual(_expand_skip_with_groups(["qtd"], groups), ["qtd"])


class DatasetTupleTests(unittest.TestCase):
    """DatasetBuilder com lookup_groups: os campos do par saem da MESMA
    tupla em todos os registros."""

    def _schema(self, quantity: int) -> SyntheticSchema:
        return SyntheticSchema(
            entity_name="CAD2D0",
            screen=ScreenSchema(
                screen_id="s1",
                fields=[
                    FieldSchema(name="modelo", datatype="text"),
                    FieldSchema(name="comb", datatype="text"),
                    FieldSchema(name="qtd", datatype="number"),
                ],
            ),
            quantity=quantity,
            seed=42,
        )

    def _groups(self):
        return {
            "fields_map": {"modelo": ("g1", 0), "comb": ("g1", 1)},
            "groups": {
                "g1": {
                    "fields": ["modelo", "comb"],
                    "tuples": [("g251", "001"), ("g252", "002"), ("h100", "001")],
                }
            },
        }

    def test_par_consistente_em_cada_registro(self):
        ds = DatasetBuilder().build(self._schema(3), lookup_groups=self._groups())
        valid = {("g251", "001"), ("g252", "002"), ("h100", "001")}
        for rec in ds.records:
            self.assertIn((rec.data["modelo"], rec.data["comb"]), valid)

    def test_primeiros_registros_seguem_a_ordem_das_tuplas(self):
        ds = DatasetBuilder().build(self._schema(2), lookup_groups=self._groups())
        self.assertEqual(ds.records[0].data["modelo"], "g251")
        self.assertEqual(ds.records[0].data["comb"], "001")
        self.assertEqual(ds.records[1].data["modelo"], "g252")

    def test_indice_alem_das_tuplas_sorteia_mantendo_o_par(self):
        ds = DatasetBuilder().build(self._schema(10), lookup_groups=self._groups())
        valid = {("g251", "001"), ("g252", "002"), ("h100", "001")}
        self.assertEqual(len(ds.records), 10)
        for rec in ds.records:
            self.assertIn((rec.data["modelo"], rec.data["comb"]), valid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
