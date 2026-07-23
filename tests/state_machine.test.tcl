package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib state_machine.tcl]

test state_machine_dispatch_ok_01 "dispatch chama handler e muda o estado" -body {
    proc ::_sm_handler_ok {spawn_id state signature norm_screen} {
        set ::_sm_called [list $spawn_id $state $signature $norm_screen]
    }
    set ::_sm_called ""
    ::state_machine::set_current_state "LOGIN"
    ::state_machine::register_rule "SIG_OK_01" "MENU" ::_sm_handler_ok
    set rc [::state_machine::dispatch "spawnX" "SIG_OK_01" "tela normalizada"]
    list $rc [::state_machine::get_current_state] $::_sm_called
} -result [list 1 "MENU" [list "spawnX" "MENU" "SIG_OK_01" "tela normalizada"]]

test state_machine_handler_missing_01 "handler inexistente: retorna 0 e NÃO muda o estado" -body {
    ::state_machine::set_current_state "ESTADO_ANTERIOR"
    ::state_machine::register_rule "SIG_MISSING_01" "ESTADO_NOVO" _sm_handler_inexistente
    set rc [::state_machine::dispatch "spawnX" "SIG_MISSING_01" "tela"]
    list $rc [::state_machine::get_current_state]
} -result [list 0 "ESTADO_ANTERIOR"]

test state_machine_handler_error_01 "erro no handler é isolado (catch) e não derruba a engine" -body {
    proc ::_sm_handler_erro {spawn_id state signature norm_screen} {
        error "falha proposital do handler"
    }
    ::state_machine::register_rule "SIG_ERR_01" "ESTADO_ERRO" ::_sm_handler_erro
    set rc [catch {::state_machine::dispatch "spawnX" "SIG_ERR_01" "tela"} err]
    # dispatch em si não propaga erro (rc do catch == 0) e retorna 0.
    list $rc $err
} -result [list 0 0]

test state_machine_priority_01 "prioridade maior vence; empate preserva ordem de registro" -body {
    proc ::_sm_handler_low {spawn_id state signature norm_screen} {
        set ::_sm_prio "low"
    }
    proc ::_sm_handler_high {spawn_id state signature norm_screen} {
        set ::_sm_prio "high"
    }
    set ::_sm_prio ""
    ::state_machine::register_rule "SIG_PRIO_01" "S_LOW" ::_sm_handler_low -priority 1
    ::state_machine::register_rule "SIG_PRIO_01" "S_HIGH" ::_sm_handler_high -priority 5
    ::state_machine::dispatch "spawnX" "SIG_PRIO_01" "tela"
    list $::_sm_prio [::state_machine::get_current_state]
} -result [list "high" "S_HIGH"]

test state_machine_when_states_01 "regra com when_states não casa fora do estado" -body {
    ::state_machine::set_current_state "OUTRO_ESTADO"
    ::state_machine::register_rule "SIG_WHEN_01" "S_WHEN" ::_sm_handler_ok -when_states {LOGIN}
    ::state_machine::dispatch "spawnX" "SIG_WHEN_01" "tela"
} -result 0

test state_machine_register_validation_01 "register_rule rejeita signature/handler vazios" -body {
    set r1 [catch {::state_machine::register_rule "" "S" "h"} e1]
    set r2 [catch {::state_machine::register_rule "SIG_V_01" "S" ""} e2]
    list $r1 $r2
} -result [list 1 1]
