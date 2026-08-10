"""Testes unitários do layout posicional de telas (screen_layout).

Cobre o formato Recital/Dakota de SAY (fTraduz) e o clássico, a associação
label↔GET por proximidade e a busca de campo por posição do cursor.
"""
from __future__ import annotations

from dakota_gateway.synthetic.screen_layout import (
    extract_layout, layout_labels, field_at,
)

SAMPLE_PRG = """\
* comentario
@ 04,00 say [*] + fTraduz(p_idioma,"Pedido","P",12,.t.,"")
@ 05,01 say fTraduz(p_idioma,"Consumidor","P",12,.t.,"")
@ 06,01 say "Valor:"
@ 04,13 get cPedido   pict p_mascdoc
@ 05,13 get cCPF      pict "@R 999.999.999-99"
@ 06,13 get nValor    pict "999.999,99"
   @ 07,13 get cFrete pict "@!"
clear gets
"""


def test_extract_layout_ftraduz_e_classico(tmp_path):
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_PRG, encoding="utf-8")
    layout = extract_layout(prg)
    by_var = {pf.var: pf for pf in layout}
    assert set(by_var) == {"cPedido", "cCPF", "nValor", "cFrete"}
    assert by_var["cPedido"].row == 4 and by_var["cPedido"].col == 13
    assert by_var["cPedido"].field == "pedido"
    assert by_var["cPedido"].label == "Pedido"
    assert by_var["cCPF"].label == "Consumidor"
    # SAY clássico com aspas também é reconhecido
    assert by_var["nValor"].label == "Valor"


def test_extract_layout_recorta_trecho(tmp_path):
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_PRG, encoding="utf-8")
    # apenas as 3 primeiras linhas de SAY: nenhum GET no trecho
    assert extract_layout(prg, 1, 4) == []
    # trecho só com GETs: campos sem label associado
    layout = extract_layout(prg, 5, 7)
    assert {pf.var for pf in layout} == {"cPedido", "cCPF", "nValor"}
    assert all(pf.label == "" for pf in layout)


def test_extract_layout_arquivo_inexistente(tmp_path):
    assert extract_layout(tmp_path / "nao-existe.prg") == []


def test_layout_labels_unicos_e_curtos_filtrados(tmp_path):
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_PRG + '@ 08,01 say fTraduz(p_idioma,"UF","U",2,.t.,"")\n',
                   encoding="utf-8")
    labels = layout_labels(extract_layout(prg))
    assert "Pedido" in labels and "Consumidor" in labels
    assert "UF" not in labels  # len < 3 é ruído


def test_field_at_exato_e_proximo(tmp_path):
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_PRG, encoding="utf-8")
    layout = extract_layout(prg)
    # posição exata do GET
    assert field_at(layout, 4, 13).var == "cPedido"
    # cursor algumas colunas à frente (digitando dentro do campo)
    assert field_at(layout, 4, 16).var == "cPedido"
    # exact=True não aceita deslocamento
    assert field_at(layout, 4, 16, exact=True) is None
    assert field_at(layout, 4, 13, exact=True).var == "cPedido"
    # linha sem GET não casa
    assert field_at(layout, 20, 13) is None
    # salto grande demais na mesma linha não casa
    assert field_at(layout, 4, 70) is None


def test_picture_literal_extraida(tmp_path):
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_PRG, encoding="utf-8")
    by_var = {pf.var: pf for pf in extract_layout(prg)}
    assert by_var["nValor"].picture == "999.999,99"
    assert by_var["cCPF"].picture == "@R 999.999.999-99"
    # pict por variável (p_mascdoc) não é literal → vazio
    assert by_var["cPedido"].picture == ""
