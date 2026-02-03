########################################################################
## signature.tcl
## Geração de assinatura estável de tela (screen signature).
##
## Ideia:
## - Focar em:
##   * Títulos (linhas superiores mais "desenhadas")
##   * Labels estáticos com ":" (Usuário:, Senha:, Opção:, etc.)
##   * Aspectos estruturais simples (qtd de linhas, colunas máximas)
## - Evitar depender de posições absolutas exatas de conteúdo dinâmico.
########################################################################

namespace eval ::signature {
    namespace export from_screen
}

proc ::signature::from_screen {norm_screen} {
    # norm_screen já vem sem ANSI, com box-drawing normalizado e quebras de linha
    # consistentes.

    set lines [split $norm_screen "\n"]

    # Remove linhas totalmente vazias no início e no fim, para maior estabilidade
    while {[llength $lines] > 0 && [string trim [lindex $lines 0]] eq ""} {
        set lines [lrange $lines 1 end]
    }
    while {[llength $lines] > 0 && [string trim [lindex $lines end]] eq ""} {
        set lines [lrange $lines 0 end-1]
    }

    # Medidas estruturais
    set numLines [llength $lines]
    set maxWidth 0
    foreach l $lines {
        set w [string length $l]
        if {$w > $maxWidth} { set maxWidth $w }
    }

    # 1) Títulos: primeiras N linhas não vazias que contêm muitos
    #    caracteres de moldura (+, -, =, |).
    set titleCandidates {}
    set N 4
    for {set i 0} {$i < $N && $i < $numLines} {incr i} {
        set line [lindex $lines $i]
        set trimmed [string trim $line]
        if {$trimmed eq ""} { continue }

        set frameCount [regexp -all {\+|\-|\=|\|} $line]
        if {$frameCount >= 3} {
            lappend titleCandidates [string trim $line]
        }
    }

    # 2) Labels estáticos: linhas contendo "palavra :"
    set labelCandidates {}
    foreach line $lines {
        set trimmed [string trim $line]
        if {$trimmed eq ""} { continue }
        if {[regexp {([[:alpha:]][[:alnum:] _]{1,20}):} $trimmed -> label]} {
            # Normaliza label (remove múltiplos espaços)
            regsub -all {\s+} $label " " labelNorm
            lappend labelCandidates $labelNorm
        }
    }

    # 3) Monta uma assinatura compacta e legível.
    #
    # Formato exemplo:
    #   L=8;W=38;TIT=...|...;LBL=Usuario:;Senha:;Opcao:

    set sigParts {}
    lappend sigParts "L=$numLines"
    lappend sigParts "W=$maxWidth"

    if {[llength $titleCandidates] > 0} {
        lappend sigParts "TIT=[join $titleCandidates {|}]"
    }

    if {[llength $labelCandidates] > 0} {
        lappend sigParts "LBL=[join $labelCandidates {;}]"
    }

    set signature [join $sigParts ";"]
    return $signature
}


