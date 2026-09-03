"""Testes do leitor de tabelas Recital (<TABELA>.<modulo>) — amostragem de
valores reais para lookup_values da síntese a partir de captura."""
from __future__ import annotations

import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dakota_gateway.source_analyzer.table_file_reader import (
    DATA_START,
    find_table_file,
    read_table,
    sample_column_values,
    sample_lookup_tables,
)


def _write_table(
    path: Path,
    fields: list[tuple[str, str, int]],
    records: list[dict | None],
) -> None:
    """Grava uma tabela Recital sintética no formato observado no legado.

    Layout: header de 3104 bytes (descritores de 24 bytes a partir de 32:
    nome 11 + tipo 1 + len u32 BE @+12 + offset u32 BE @+20), área de nomes
    de 3552 bytes, registros a partir de 6656 — flag (' '/'*') + campos.
    ``records``: dict campo→valor, ou None para registro deletado.
    """
    offsets: list[int] = []
    pos = 1
    for _name, _type, length in fields:
        offsets.append(pos)
        pos += length
    rec_len = pos
    header = bytearray(3104)
    struct.pack_into(">I", header, 0, len(records))
    struct.pack_into(">I", header, 16, 3104)
    struct.pack_into(">I", header, 20, rec_len)
    struct.pack_into(">I", header, 28, len(fields))
    off = 32
    for (name, ftype, length), foff in zip(fields, offsets):
        header[off:off + 11] = name.encode("ascii")[:11].ljust(11, b"\x00")
        header[off + 11] = ord(ftype)
        struct.pack_into(">I", header, off + 12, length)
        struct.pack_into(">I", header, off + 20, foff)
        off += 24
    body = bytearray()
    for rec in records:
        row = bytearray(rec_len)
        row[0] = ord("*") if rec is None else ord(" ")
        if rec is not None:
            for (name, ftype, length), foff in zip(fields, offsets):
                value = str(rec.get(name, ""))
                if ftype == "N":
                    encoded = value.rjust(length)[:length].encode("latin-1")
                else:
                    encoded = value.ljust(length)[:length].encode("latin-1")
                row[foff:foff + length] = encoded
        body += row
    path.write_bytes(bytes(header) + bytes(3552) + bytes(body))


def _write_index(path: Path, expression: str) -> None:
    path.write_bytes(expression.encode("ascii") + b"\x00" * (512 - len(expression)))


class ReadTableTests(unittest.TestCase):
    def test_le_schema_e_conta_registros(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "arq210.cmp"
            _write_table(
                path,
                [("CODIGO", "C", 3), ("DESCRICAO", "C", 20)],
                [{"CODIGO": "001"}, {"CODIGO": "002"}, None],
            )
            table = read_table(path)
        self.assertIsNotNone(table)
        self.assertEqual(table.record_length, 24)
        self.assertEqual(table.record_count, 3)
        self.assertEqual(table.data_start, DATA_START)
        self.assertEqual(
            [(f.name, f.type, f.length, f.offset) for f in table.fields],
            [("CODIGO", "C", 3, 1), ("DESCRICAO", "C", 20, 4)],
        )

    def test_rec_len_inconsistente_rejeita(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "arq210.cmp"
            _write_table(path, [("CODIGO", "C", 3)], [{"CODIGO": "001"}])
            raw = bytearray(path.read_bytes())
            struct.pack_into(">I", raw, 20, 99)  # rec_len não casa com os campos
            path.write_bytes(bytes(raw))
            self.assertIsNone(read_table(path))

    def test_arquivo_truncado_rejeita(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "arq210.cmp"
            _write_table(path, [("CODIGO", "C", 3)], [{"CODIGO": "001"}] * 3)
            raw = path.read_bytes()
            path.write_bytes(raw[:-3])  # tamanho deixa de ser múltiplo de rec_len
            self.assertIsNone(read_table(path))

    def test_inexistente_rejeita(self):
        self.assertIsNone(read_table("/nao/existe/arq210.cmp"))

    def test_nome_de_campo_invalido_interrompe_descritores(self):
        """Descritor com nome não-ASCII encerra a lista (área zerada)."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "arq210.cmp"
            _write_table(path, [("CODIGO", "C", 3)], [{"CODIGO": "001"}])
            table = read_table(path)
            self.assertIsNotNone(table)
            self.assertEqual(len(table.fields), 1)  # parou no padding zerado


class SampleColumnValuesTests(unittest.TestCase):
    def test_amostra_so_ativos_dedup_na_ordem(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "arq210.cmp"
            _write_table(
                path,
                [("CODIGO", "C", 3), ("DESCRICAO", "C", 20)],
                [
                    {"CODIGO": "001", "DESCRICAO": "28 DIAS"},
                    None,  # deletado
                    {"CODIGO": "002", "DESCRICAO": "14 DIAS"},
                    {"CODIGO": "001", "DESCRICAO": "dup"},
                    {"CODIGO": "", "DESCRICAO": "vazio"},
                    {"CODIGO": "003", "DESCRICAO": "A VISTA"},
                ],
            )
            table = read_table(path)
            self.assertIsNotNone(table)
            self.assertEqual(
                sample_column_values(table, "codigo"), ["001", "002", "003"]
            )

    def test_coluna_inexistente_retorna_vazio(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "arq210.cmp"
            _write_table(path, [("CODIGO", "C", 3)], [{"CODIGO": "001"}])
            table = read_table(path)
            self.assertEqual(sample_column_values(table, "NAOEXISTE"), [])

    def test_limite_com_stride_cobre_o_arquivo(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "arq310.cmp"
            _write_table(
                path,
                [("NUMERO", "C", 6), ("FORNECEDOR", "C", 8)],
                [{"FORNECEDOR": f"30001{i:03d}"} for i in range(100)],
            )
            table = read_table(path)
            valores = sample_column_values(table, "FORNECEDOR", limit=10)
        self.assertEqual(len(valores), 10)
        # stride: amostras espalhadas, não só o começo do arquivo
        self.assertIn("30001090", valores)  # último stride cobre o fim

    def test_campo_data_nao_e_amostrado(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "arq310.cmp"
            _write_table(
                path,
                [("NUMERO", "C", 6), ("EMISSAO", "D", 4)],
                [{"NUMERO": "100000", "EMISSAO": "\x00\x00\x9b\x35"}],
            )
            table = read_table(path)
            self.assertEqual(sample_column_values(table, "EMISSAO"), [])


class FindTableFileTests(unittest.TestCase):
    def test_encontra_por_stem_qualquer_extensao(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_table(base / "arq210.cmp", [("CODIGO", "C", 3)], [{"CODIGO": "1"}])
            self.assertEqual(find_table_file([str(base)], "ARQ210"), base / "arq210.cmp")

    def test_ignora_arquivo_de_indice(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "iarq210.001").write_bytes(b"codigo".ljust(512, b"\x00"))
            self.assertIsNone(find_table_file([str(base)], "arq210"))

    def test_arquivo_nao_parseavel_e_pulado(self):
        """Objeto compilado (.dbo) com o nome lógico não é dado — o leitor
        segue para o próximo candidato."""
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "est361.dbo").write_bytes(b"duz(p_idioma)...")
            _write_table(base / "est361.est", [("CODIGO", "C", 3)], [{"CODIGO": "1"}])
            self.assertEqual(find_table_file([str(base)], "est361"), base / "est361.est")

    def test_busca_em_varios_diretorios(self):
        with TemporaryDirectory() as a, TemporaryDirectory() as b:
            _write_table(Path(b) / "est281.est", [("CODIGO", "C", 2)], [{"CODIGO": "1"}])
            self.assertEqual(
                find_table_file([a, b], "est281"), Path(b) / "est281.est"
            )

    def test_nome_logico_modNNN_resolve_para_arqNNN(self):
        """Convenção física do legado: use est361 → dado em arq361.est."""
        with TemporaryDirectory() as tmp:
            est = Path(tmp) / "est"
            est.mkdir()
            _write_table(
                est / "arq361.est",
                [("PEDIDO", "C", 6), ("ITEM", "C", 2)],
                [{"PEDIDO": "000001"}],
            )
            self.assertEqual(
                find_table_file([str(est)], "est361"), est / "arq361.est"
            )

    def test_hint_de_modulo_desempata_arq_replicado(self):
        """arq<NNN> existe por módulo com conteúdos diferentes — o módulo da
        captura decide."""
        with TemporaryDirectory() as tmp:
            cad = Path(tmp) / "cad"
            cmp_ = Path(tmp) / "cmp"
            cad.mkdir()
            cmp_.mkdir()
            _write_table(cad / "arq210.cad", [("CODIGO", "C", 3)], [{"CODIGO": "C1"}])
            _write_table(cmp_ / "arq210.cmp", [("CODIGO", "C", 3)], [{"CODIGO": "P1"}])
            dirs = [str(cad), str(cmp_)]
            self.assertEqual(find_table_file(dirs, "arq210", "cmp"), cmp_ / "arq210.cmp")
            self.assertEqual(find_table_file(dirs, "arq210"), cad / "arq210.cad")


class SampleLookupTablesTests(unittest.TestCase):
    def test_amostra_pela_primeira_chave_do_indice(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_table(
                base / "arq210.cmp",
                [("CODIGO", "C", 3), ("DESCRICAO", "C", 20)],
                [{"CODIGO": "001"}, {"CODIGO": "002"}, {"CODIGO": "003"}],
            )
            _write_index(base / "iarq210.001", "codigo")
            sampled = sample_lookup_tables([str(base)], {"arq210"})
        self.assertEqual(sampled, {"arq210": ["001", "002", "003"]})

    def test_chave_composta_amostra_primeiro_campo(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_table(
                base / "est361.est",
                [("REDE", "C", 3), ("LOJA", "C", 3), ("PRODUTO", "C", 8)],
                [{"REDE": "001", "LOJA": "001", "PRODUTO": "P1"}],
            )
            _write_index(base / "iest361.001", "rede + loja")
            sampled = sample_lookup_tables([str(base)], {"est361"})
        self.assertEqual(sampled, {"est361": ["001"]})

    def test_sem_indice_amostra_primeiro_campo_C(self):
        """Tabela de cadastro auxiliar sem índice próprio (ex.: arq210.cmp
        de condições de pagamento): cai para o primeiro campo C — convenção
        do legado, o código abre o cadastro."""
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_table(
                base / "arq210.cmp",
                [("CODIGO", "C", 3), ("DESCONTO", "N", 6)],
                [{"CODIGO": "001"}, {"CODIGO": "002"}],
            )
            self.assertEqual(
                sample_lookup_tables([str(base)], {"arq210"}),
                {"arq210": ["001", "002"]},
            )

    def test_sem_indice_e_sem_campo_C_nao_amostra(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_table(
                base / "arq210.cmp",
                [("DESCONTO", "N", 6)],
                [{"DESCONTO": "5.00"}],
            )
            self.assertEqual(sample_lookup_tables([str(base)], {"arq210"}), {})

    def test_fallback_prefere_campo_com_nome_de_codigo(self):
        """Sem índice, campo C com nome de código (NUMERO/CODIGO) vence o
        primeiro campo C — arq310 abre com TIPOOC mas a chave é NUMERO."""
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_table(
                base / "arq310.cmp",
                [("TIPOOC", "C", 2), ("NUMERO", "C", 6)],
                [{"TIPOOC": "01", "NUMERO": "100000"}],
            )
            self.assertEqual(
                sample_lookup_tables([str(base)], {"arq310"}),
                {"arq310": ["100000"]},
            )

    def test_sem_arquivo_de_dados_nao_amostra(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(sample_lookup_tables([tmp], {"arq210"}), {})

    def test_tabela_corrompida_nao_derruba(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "arq210.cmp").write_bytes(b"lixo")
            _write_index(base / "iarq210.001", "codigo")
            self.assertEqual(sample_lookup_tables([str(base)], {"arq210"}), {})

    def test_nome_logico_usa_indice_logico_e_dado_arq(self):
        """est361: índice iest361.001 (pedido + item) + dado arq361.est —
        amostra PEDIDO do arquivo físico."""
        with TemporaryDirectory() as tmp:
            est = Path(tmp) / "est"
            est.mkdir()
            _write_table(
                est / "arq361.est",
                [("ECOMMERCE", "C", 1), ("PEDIDO", "C", 6), ("ITEM", "C", 2)],
                [{"PEDIDO": "100001"}, {"PEDIDO": "100002"}, None],
            )
            _write_index(est / "iest361.001", "pedido + item")
            _write_table(est / "est361.est", [("X", "C", 1)], [{"X": "0"}])  # par p/ scan
            sampled = sample_lookup_tables([str(est)], {"est361"})
        self.assertEqual(sampled, {"est361": ["100001", "100002"]})

    def test_hint_de_modulo_em_dict(self):
        with TemporaryDirectory() as tmp:
            cad = Path(tmp) / "cad"
            cmp_ = Path(tmp) / "cmp"
            cad.mkdir()
            cmp_.mkdir()
            _write_table(cad / "arq210.cad", [("CODIGO", "C", 3)], [{"CODIGO": "C1"}])
            _write_table(cmp_ / "arq210.cmp", [("CODIGO", "C", 3)], [{"CODIGO": "P1"}])
            sampled = sample_lookup_tables(
                [str(cad), str(cmp_)], {"arq210": "cmp"})
        self.assertEqual(sampled, {"arq210": ["P1"]})


if __name__ == "__main__":
    unittest.main()
