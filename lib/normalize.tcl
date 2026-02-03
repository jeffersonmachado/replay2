########################################################################
## normalize.tcl
## Normalização de tela:
## - Remoção de sequências ANSI de controle
## - Normalização de caracteres box-drawing
## - Normalização de espaços/brancos
########################################################################

namespace eval ::normalize {
    namespace export screen

    # Mapa simples de box-drawing Unicode -> ASCII aproximado
    variable boxMap {
        "─" "-"  "━" "-"
        "│" "|"  "┃" "|"
        "┌" "+"  "┏" "+"
        "┐" "+"  "┓" "+"
        "└" "+"  "┗" "+"
        "┘" "+"  "┛" "+"
        "├" "+"  "┣" "+"
        "┤" "+"  "┫" "+"
        "┬" "+"  "┳" "+"
        "┴" "+"  "┻" "+"
        "┼" "+"  "╋" "+"
        "═" "="
        "║" "|"
        "╔" "+" "╗" "+"
        "╚" "+" "╝" "+"
        "╠" "+" "╣" "+"
        "╦" "+" "╩" "+"
        "╬" "+"
    }
}

proc ::normalize::strip_ansi {text} {
    # Remove sequências ANSI de controle comuns, incluindo ESC[..m,
    # ESC[..H, ESC[2J etc.
    #
    # Padrão aproximado: \x1B[ ... letras
    regsub -all {\x1B\[[0-9;?]*[A-Za-z]} $text "" text
    # Outras sequências iniciadas por ESC que não seguem o padrão acima
    regsub -all {\x1B.} $text "" text
    return $text
}

proc ::normalize::normalize_boxes {text} {
    variable boxMap
    return [string map $boxMap $text]
}

proc ::normalize::normalize_whitespace {text} {
    # Normaliza quebras de linha para \n
    regsub -all {\r\n} $text "\n" text
    regsub -all {\r} $text "\n" text

    # Remove espaços à direita em cada linha, e colapsa linhas vazias múltiplas.
    set out {}
    set emptyCount 0
    foreach line [split $text "\n"] {
        # Trim à direita apenas
        regsub {\s+$} $line "" line

        if {$line eq ""} {
            incr emptyCount
            # No máximo 1 linha vazia consecutiva
            if {$emptyCount > 1} {
                continue
            }
        } else {
            set emptyCount 0
        }
        lappend out $line
    }

    return [join $out "\n"]
}

proc ::normalize::screen {raw_text} {
    # Pipeline de normalização de tela.
    set txt $raw_text
    set txt [strip_ansi $txt]
    set txt [normalize_boxes $txt]
    set txt [normalize_whitespace $txt]
    return $txt
}


