########################################################################
## state_machine.tcl
## Máquina de estados orientada a assinatura de tela.
##
## Conceitos:
## - Estado lógico atual (ex: LOGIN, MENU, CADASTRO_CLIENTE, ...)
## - Assinaturas conhecidas de tela associadas a handlers
## - Handlers isolados por módulo (ex: screens/screen_login.tcl)
##
## API pública:
##   ::state_machine::register sig state handlerProc
##   ::state_machine::set_current_state stateName
##   ::state_machine::get_current_state
##   ::state_machine::dispatch spawn_id signature norm_screen
########################################################################

namespace eval ::state_machine {
    namespace export register set_current_state get_current_state dispatch

    # Mapa: assinatura -> dict com {state <nome> handler <proc-name>}
    variable handlers
    array set handlers {}

    variable currentState ""
}

proc ::state_machine::register {signature state handlerProc} {
    # Registra uma assinatura de tela conhecida e o handler correspondente.
    variable handlers
    set key $signature
    set handlers($key) [dict create state $state handler $handlerProc]
}

proc ::state_machine::set_current_state {stateName} {
    variable currentState
    set currentState $stateName
}

proc ::state_machine::get_current_state {} {
    variable currentState
    return $currentState
}

proc ::state_machine::dispatch {spawn_id signature norm_screen} {
    # Tenta encontrar um handler para a assinatura fornecida.
    # Retorna 1 se algum handler foi chamado, 0 caso contrário.

    variable handlers
    variable currentState

    # Neste design, usamos assinatura como chave primária. Opcionalmente,
    # poderíamos exigir match também do estado atual.
    if {![info exists handlers($signature)]} {
        return 0
    }

    set info $handlers($signature)
    set state   [dict get $info state]
    set handler [dict get $info handler]

    # Atualiza o estado atual para o estado associado a esta assinatura.
    set_current_state $state

    if {![llength [info procs $handler]]} {
        puts stderr "Aviso: handler '$handler' não existe (assinatura: $signature)"
        return 0
    }

    # Chamamos o handler com:
    #   spawn_id    - canal Expect
    #   state       - estado lógico atual
    #   signature   - assinatura de tela detectada
    #   norm_screen - tela normalizada completa
    $handler $spawn_id $state $signature $norm_screen

    return 1
}


