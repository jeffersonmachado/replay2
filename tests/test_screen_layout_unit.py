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


SAMPLE_GRID_PRG = """\
* grade de itens (dbedit) + GETs clássicos
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


def test_extract_layout_grade_dbedit(tmp_path):
    """dbedit vira células com span: largura = max(cabeçalho, PICTURE),
    separador de 1 coluna; coluna com expressão (right(item,2)) só ocupa
    largura, não vira campo."""
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_GRID_PRG, encoding="utf-8")
    layout = extract_layout(prg)
    grids = [pf for pf in layout if pf.is_grid_cell]
    by_var = {pf.var: pf for pf in grids}
    assert set(by_var) == {"modelo", "qtd"}  # right(item,2) não vira campo
    # item: 0-1 (w=2), sep, modelo: 3-8 (w=6), sep, qtd: 10-13 (w=4)
    assert (by_var["modelo"].row, by_var["modelo"].row_end) == (16, 21)
    assert (by_var["modelo"].col, by_var["modelo"].col_end) == (3, 8)
    assert (by_var["qtd"].col, by_var["qtd"].col_end) == (10, 13)
    assert by_var["qtd"].label == "Qtd" and by_var["qtd"].picture == "9999"


def test_field_at_grade_por_span(tmp_path):
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_GRID_PRG, encoding="utf-8")
    layout = extract_layout(prg)
    # cursor dentro do span da célula (posição pós-eco), em qualquer linha
    assert field_at(layout, 19, 4, value="g2511").var == "modelo"
    assert field_at(layout, 19, 8, value="g2511").var == "modelo"
    assert field_at(layout, 17, 12, value="3").var == "qtd"
    # fora do span da grade
    assert field_at(layout, 19, 9, value="x") is None
    assert field_at(layout, 22, 4, value="x") is None
    # GET clássico continua valendo (fora da região da grade)
    assert field_at(layout, 7, 13, value="1").var == "cFrete"


def test_field_at_grade_desempate(tmp_path):
    """Grades sobrepostas (comum em fontes reais): decimal → PICTURE com
    vírgula; dígitos → prefere PICTURE numérica; texto em célula numérica
    é rejeitado; resto → célula mais estreita."""
    prg = tmp_path / "xx361.prg"
    prg.write_text(
        SAMPLE_GRID_PRG
        # grades sobrepostas cobrindo a coluna 12: tam(11-13, texto) e
        # valor(12-21, decimal) — junto com qtd(10-13, numérica) da 1ª grade
        + 'dbedit(15,11,21,40,vCam2,"fOutra",vPict2,vCol2)\n'
        + 'vCam2[01] = "tam"\n'
        + 'vPict2[01] = "@"\n'
        + 'vCol2[01] = fTraduz(p_idioma,"Tam","P",3,.f.,"")\n'
        + 'dbedit(15,12,21,40,vCam3,"fOutra2",vPict3,vCol3)\n'
        + 'vCam3[01] = "valor"\n'
        + 'vPict3[01] = "999.999,99"\n'
        + 'vCol3[01] = fTraduz(p_idioma,"Valor","P",10,.f.,"")\n',
        encoding="utf-8")
    layout = extract_layout(prg)
    # dígitos → prefere PICTURE numérica (qtd 9999)
    assert field_at(layout, 19, 12, value="1").var == "qtd"
    # decimal → célula com vírgula na PICTURE
    assert field_at(layout, 19, 12, value="229,9").var == "valor"
    # texto → célula textual (numéricas rejeitam valor não numérico)
    assert field_at(layout, 19, 12, value="abc").var == "tam"
