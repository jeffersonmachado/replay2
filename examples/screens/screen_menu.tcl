########################################################################
## screen_menu.tcl (EXEMPLO)
## Exemplo de tela de MENU PRINCIPAL e seu handler.
########################################################################

namespace eval ::screen_menu {
    namespace export init handler
    variable expected_signature ""
}

proc ::screen_menu::build_example_screen {} {
    return [string trim {
+======================================+
|          MENU PRINCIPAL             |
+======================================+
|  1 - Cadastros                      |
|  2 - Relatórios                     |
|  3 - Utilitários                    |
+======================================+

Opção: _
}]
}

proc ::screen_menu::init {} {
    variable expected_signature
    set example [::screen_menu::build_example_screen]
    set expected_signature [::signature::from_screen $example]
    ::state_machine::register $expected_signature "MENU" "::screen_menu::handler"
}

proc ::screen_menu::handler {spawn_id state signature norm_screen} {
    variable expected_signature

    if {$signature ne $expected_signature} {
        puts stderr "screen_menu::handler chamado com assinatura inesperada"
    }

    puts ">> [clock format [clock seconds]] - Detectada tela de MENU (state=$state)"
    fconfigure $spawn_id -encoding utf-8 -translation crlf
    send -- "3\n"
    after 500
    puts ">> Fluxo de exemplo concluído. Encerrando."
    exit 0
}

::screen_menu::init

