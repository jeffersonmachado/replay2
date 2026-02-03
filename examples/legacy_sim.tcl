#!/usr/bin/env tclsh
#
# legacy_sim.tcl
# Backend simulado de sistema legado em modo texto (EXEMPLO).
#

fconfigure stdin  -encoding utf-8 -translation crlf
fconfigure stdout -encoding utf-8 -translation crlf

proc clear_screen {} {
    puts "\033\[2J\033\[H"
}

proc show_login {} {
    clear_screen
    puts "╔══════════════════════════════════════╗"
    puts "║        SISTEMA LEGADO DEMO          ║"
    puts "╠══════════════════════════════════════╣"
    puts "║  Usuário :                          ║"
    puts "║  Senha  :                          ║"
    puts "╚══════════════════════════════════════╝"
    puts ""
    puts "F3=Ajuda   F10=Sair"
    flush stdout
}

proc show_menu {} {
    clear_screen
    puts "╔══════════════════════════════════════╗"
    puts "║          MENU PRINCIPAL             ║"
    puts "╠══════════════════════════════════════╣"
    puts "║  1 - Cadastros                      ║"
    puts "║  2 - Relatórios                     ║"
    puts "║  3 - Utilitários                    ║"
    puts "╚══════════════════════════════════════╝"
    puts ""
    puts "Opção: _"
    flush stdout
}

show_login
while {1} {
    if {[gets stdin line] < 0} { break }
    # Quando rodando atrás de um pty (Expect), é comum o Enter virar '\r'.
    # Normalizamos para facilitar a simulação.
    set line [string trim $line "\r\n"]
    switch -exact -- $line {
        "__LOGIN_OK__" {
            show_menu
        }
        default {
            # Ignora qualquer outra entrada
        }
    }
}

