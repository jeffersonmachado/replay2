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
##   ::state_machine::register_rule sig setState handlerProc ?opts?
##   ::state_machine::set_current_state stateName
##   ::state_machine::get_current_state
##   ::state_machine::dispatch spawn_id signature norm_screen
##   ::state_machine::list_rules ?signature?
########################################################################

namespace eval ::state_machine {
    namespace export register register_rule set_current_state get_current_state dispatch list_rules

    # Mapa: assinatura -> lista de regras (cada regra é um dict)
    # Regra:
    #   id           string (único)
    #   signature    string
    #   set_state    string (estado a ser setado quando casar)
    #   handler      procName
    #   priority     int (maior vence)
    #   when_states  lista (opcional) de estados em que esta regra é válida
    #   predicate    procName (opcional) -> retorna 1/0
    variable rulesBySig
    array set rulesBySig {}

    variable currentState ""

    variable nextRuleId 0
}

proc ::state_machine::register {signature state handlerProc} {
    # Compat: API antiga registra uma regra simples (prioridade 0).
    return [::state_machine::register_rule $signature $state $handlerProc]
}

proc ::state_machine::register_rule {signature setState handlerProc args} {
    variable rulesBySig
    variable nextRuleId

    if {$signature eq ""} { error "state_machine::register_rule: signature vazia" }
    if {$handlerProc eq ""} { error "state_machine::register_rule: handlerProc vazio" }

    array set opts {
        -priority 0
        -when_states {}
        -predicate ""
        -id ""
    }
    if {[llength $args] % 2 != 0} {
        error "state_machine::register_rule: opções devem ser pares chave/valor"
    }
    array set opts $args

    set id $opts(-id)
    if {$id eq ""} {
        incr nextRuleId
        set id "rule#$nextRuleId"
    }

    set rule [dict create \
        id $id \
        signature $signature \
        set_state $setState \
        handler $handlerProc \
        priority [expr {int($opts(-priority))}] \
        when_states $opts(-when_states) \
        predicate $opts(-predicate) \
    ]

    if {![info exists rulesBySig($signature)]} {
        set rulesBySig($signature) {}
    }
    lappend rulesBySig($signature) $rule

    # Evento (se disponível)
    if {[llength [info procs ::events::emit]]} {
        ::events::emit "rule_registered" [dict create \
            signature $signature \
            rule_id $id \
            set_state $setState \
            handler $handlerProc \
            priority [dict get $rule priority] \
        ]
    }

    return $id
}

proc ::state_machine::set_current_state {stateName} {
    variable currentState
    set currentState $stateName
}

proc ::state_machine::get_current_state {} {
    variable currentState
    return $currentState
}

proc ::state_machine::list_rules {{signature ""}} {
    variable rulesBySig
    if {$signature eq ""} {
        set out {}
        foreach sig [lsort [array names rulesBySig]] {
            foreach rule $rulesBySig($sig) {
                lappend out $rule
            }
        }
        return $out
    }
    if {![info exists rulesBySig($signature)]} { return {} }
    return $rulesBySig($signature)
}

proc ::state_machine::_rule_matches {rule spawn_id signature norm_screen} {
    variable currentState

    # when_states: se definido, só vale se o estado atual estiver presente
    set whenStates [dict get $rule when_states]
    if {[llength $whenStates] > 0} {
        if {[lsearch -exact $whenStates $currentState] < 0} {
            return 0
        }
    }

    # predicate opcional
    set pred [dict get $rule predicate]
    if {$pred ne ""} {
        if {![llength [info procs $pred]]} {
            # Predicate inexistente => não casa
            if {[llength [info procs ::events::emit]]} {
                ::events::emit "predicate_missing" [dict create \
                    level "warn" \
                    signature $signature \
                    state $currentState \
                    rule_id [dict get $rule id] \
                    predicate $pred \
                ]
            }
            return 0
        }
        set ok 0
        set rc [catch { set ok [$pred $spawn_id $currentState $signature $norm_screen] } err]
        if {$rc != 0} {
            if {[llength [info procs ::events::emit]]} {
                ::events::emit "predicate_error" [dict create \
                    level "warn" \
                    signature $signature \
                    state $currentState \
                    rule_id [dict get $rule id] \
                    predicate $pred \
                    error $err \
                ]
            }
            return 0
        }
        if {![expr {int($ok) != 0}]} {
            return 0
        }
    }

    return 1
}

proc ::state_machine::dispatch {spawn_id signature norm_screen} {
    # Tenta encontrar um handler para a assinatura fornecida.
    # Retorna 1 se algum handler foi chamado, 0 caso contrário.

    variable rulesBySig
    variable currentState

    if {![info exists rulesBySig($signature)]} {
        return 0
    }

    set candidates $rulesBySig($signature)
    set matched {}
    foreach rule $candidates {
        if {[::state_machine::_rule_matches $rule $spawn_id $signature $norm_screen]} {
            lappend matched $rule
        }
    }

    if {[llength $matched] < 1} {
        if {[llength [info procs ::events::emit]]} {
            ::events::emit "route_decision" [dict create \
                level "debug" \
                signature $signature \
                state $currentState \
                candidates_count [llength $candidates] \
                matched_count 0 \
                chosen "" \
                reason "no_rule_matched" \
            ]
        }
        return 0
    }

    # Escolhe por prioridade (maior vence). Em empate, preserva ordem de registro.
    set chosen [lindex $matched 0]
    foreach rule $matched {
        if {[dict get $rule priority] > [dict get $chosen priority]} {
            set chosen $rule
        }
    }

    set state   [dict get $chosen set_state]
    set handler [dict get $chosen handler]

    # Atualiza o estado atual para o estado associado a esta assinatura.
    set_current_state $state

    if {![llength [info procs $handler]]} {
        puts stderr "Aviso: handler '$handler' não existe (assinatura: $signature)"
        if {[llength [info procs ::events::emit]]} {
            ::events::emit "handler_missing" [dict create \
                level "warn" \
                signature $signature \
                state $currentState \
                handler $handler \
                rule_id [dict get $chosen id] \
            ]
        }
        return 0
    }

    if {[llength [info procs ::events::emit]]} {
        ::events::emit "route_decision" [dict create \
            level "debug" \
            signature $signature \
            state $currentState \
            candidates_count [llength $candidates] \
            matched_count [llength $matched] \
            chosen [dict create \
                rule_id [dict get $chosen id] \
                handler $handler \
                set_state $state \
                priority [dict get $chosen priority] \
            ] \
            reason "priority" \
        ]
        ::events::emit "handler_called" [dict create \
            level "debug" \
            signature $signature \
            state $currentState \
            handler $handler \
            rule_id [dict get $chosen id] \
        ]
    }

    # Chamamos o handler com:
    #   spawn_id    - canal Expect
    #   state       - estado lógico atual
    #   signature   - assinatura de tela detectada
    #   norm_screen - tela normalizada completa
    $handler $spawn_id $state $signature $norm_screen

    return 1
}


