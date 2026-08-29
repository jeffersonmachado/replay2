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


SAMPLE_VALID_PRG = """\
* VALID na linha de continuação (estilo real do est361.prg)
@ 06,13 get nDesconto pict "999.999,99" ;
            valid(fValidExit() and nDesconto >= 0)

@ 06,60 get nValFrete pict "999.999,99" ;
            valid(lastkey() = 5 or  nValFrete >= 0)

@ 09,48 get nVolumes pict "999" ;
            valid(lastkey() = 5 or nVolumes > 0)

@ 10,13 get cEmail && valid(lastkey() = 5 or fValEmail(@cEmail,10,13,.f.))
@ 11,13 get cLogrado pict "@!" ;
            valid(lastkey() = 5 or !empty(cLogrado))
@ 12,13 get nNota valid nNota > 0
* @ 13,13 get cComent pict "99" valid(cComent > 0)
"""


def test_valid_em_linha_de_continuacao(tmp_path):
    """VALID após `;` (linha seguinte do GET) é extraído; VALID em
    comentário `&&`/`*` é ignorado; `valid expr` sem parêntese também."""
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_VALID_PRG, encoding="utf-8")
    by_var = {pf.var: pf for pf in extract_layout(prg)}
    assert by_var["nDesconto"].valid_expr == "fValidExit() and nDesconto >= 0"
    assert by_var["nValFrete"].valid_expr == "lastkey() = 5 or  nValFrete >= 0"
    assert by_var["nVolumes"].valid_expr == "lastkey() = 5 or nVolumes > 0"
    assert by_var["nNota"].valid_expr == "nNota > 0"
    # comentários não carregam VALID
    assert by_var["cEmail"].valid_expr == ""
    assert "cComent" not in by_var
    assert by_var["cLogrado"].valid_expr == "lastkey() = 5 or !empty(cLogrado)"
    # PICTURE da própria linha continua sendo extraída
    assert by_var["nDesconto"].picture == "999.999,99"


SAMPLE_DUP_PRG = """\
* rotina de consulta (sem VALID) — mesmas coordenadas da inclusão
   @ 06,13 get nDesconto pict "999.999,99"
   @ 07,13 get cFrete    pict "@!"
* rotina de inclusão (com VALID) — est361.prg: ~747 x ~1071
@ 06,13 get nDesconto pict "999.999,99" ;
            valid(fValidExit() and nDesconto >= 0)
@ 07,13 get cFrete pict "99" prefield pCons postfield pFecha ;
            valid(lastkey() = 5 or fValida(strzero(cFrete,2),strzero(cFrete,2),[arqfrete],[descricao],7,16,[]))
"""


def test_get_duplicado_herda_valid_da_redeclaracao(tmp_path):
    """O mesmo `@ row,col get` redeclarado em outra rotina (consulta x
    inclusão) é dedupado pela 1ª ocorrência, mas herda o VALID da 2ª."""
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_DUP_PRG, encoding="utf-8")
    layout = extract_layout(prg)
    by_var = {pf.var: pf for pf in layout}
    assert by_var["nDesconto"].valid_expr == "fValidExit() and nDesconto >= 0"
    assert by_var["cFrete"].valid_expr.startswith("lastkey() = 5 or fValida(")
    # posição e PICTURE da 1ª ocorrência são preservadas
    assert by_var["cFrete"].picture == "@!"


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


SAMPLE_GRID_SOURCE_PRG = """\
function fCriaTmp6
use &earqtmp6 in 0 alias arqtmp6
public vCamTmp6[2]
vCamTmp6[1] = "codigo"
vCamTmp6[2] = "valor"
vPictTmp6[1] = "99"
vPictTmp6[2] = "999.999,99"
vColTmp6[1] = fTraduz(p_idioma,"Cd","P",2,.f.,"")
vColTmp6[2] = fTraduz(p_idioma,"Valor","P",10,.f.,"")
if seek(cPedido,est366)
   do while !eof()
      select arqtmp6
      append blank
      replace codigo with est366->formapag
      replace valor  with est366->valor
   enddo
endif
return
function fTela
@ 07,13 get cFrete pict "@!"
dbedit(13,01,19,78,vCamTmp6,"fTabelaPag",vPictTmp6,vColTmp6)
return
"""


def test_extract_layout_grade_com_tabela_origem(tmp_path):
    """A grade dbedit edita um alias temporário; o `with X->` do
    preenchimento na mesma função denuncia a tabela real (est366).
    GET clássico fica origin=form, sem tabela."""
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_GRID_SOURCE_PRG, encoding="utf-8")
    layout = extract_layout(prg)
    by_var = {pf.var: pf for pf in layout}
    assert by_var["codigo"].origin == "grid"
    assert by_var["codigo"].grid_source == "est366"
    assert by_var["valor"].grid_source == "est366"
    assert by_var["cFrete"].origin == "form"
    assert by_var["cFrete"].grid_source == ""


def test_extract_layout_grade_sem_preenchimento_sem_origem(tmp_path):
    """Sem `with X->` identificável, grid_source fica vazio (não inventa)."""
    prg = tmp_path / "xx361.prg"
    prg.write_text(SAMPLE_GRID_PRG, encoding="utf-8")
    layout = extract_layout(prg)
    grids = [pf for pf in layout if pf.is_grid_cell]
    assert grids and all(pf.origin == "grid" for pf in grids)
    assert all(pf.grid_source == "" for pf in grids)
