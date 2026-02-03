########################################################################
## screen_login.tcl (EXEMPLO)
## Exemplo de tela de LOGIN e seu handler.
########################################################################

namespace eval ::screen_login {
    namespace export init handler
    variable expected_signature ""
}

proc ::screen_login::build_example_screen {} {
    # Tela "normalizada" aproximada da saída do backend simulado.
    return [string trim {
+======================================+
|        SISTEMA LEGADO DEMO          |
+======================================+
|  Usuário :                          |
|  Senha  :                          |
+======================================+

F3=Ajuda   F10=Sair
}]
}

proc ::screen_login::init {} {
    variable expected_signature

    set example [::screen_login::build_example_screen]
    set expected_signature [::signature::from_screen $example]
    ::state_machine::register $expected_signature "LOGIN" "::screen_login::handler"
}

proc ::screen_login::handler {spawn_id state signature norm_screen} {
    variable expected_signature

    if {$signature ne $expected_signature} {
        puts stderr "screen_login::handler chamado com assinatura inesperada"
    }

    puts ">> [clock format [clock seconds]] - Detectada tela de LOGIN (state=$state)"
    fconfigure $spawn_id -encoding utf-8 -translation crlf
    send -- "__LOGIN_OK__\n"
}

::screen_login::init

