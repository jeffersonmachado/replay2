########################################################################
## action.tcl
## API única para “efeitos” (send/sleep/fconfigure) com emissão de eventos.
##
## Objetivos:
## - Permitir record/replay e auditoria de ações
## - Isolar o uso de Expect (`send`) em um único lugar
## - Manter handlers simples e testáveis
########################################################################

namespace eval ::action {
    namespace export send_keys sleep_ms configure_channel
}

proc ::action::_emit {type payload} {
    if {[llength [info procs ::events::emit]]} {
        ::events::emit $type $payload
    }
}

proc ::action::send_keys {spawn_id keys} {
    if {![llength [info commands send]]} {
        error "action::send_keys requer Expect (comando 'send' indisponível)"
    }
    # Preferimos endereçar explicitamente o spawn_id.
    send -i $spawn_id -- $keys
    ::action::_emit "action_sent" [dict create \
        level "debug" \
        spawn_id $spawn_id \
        action "send_keys" \
        keys $keys \
        keys_len [string length $keys] \
    ]
}

proc ::action::sleep_ms {ms} {
    set t [expr {int($ms)}]
    if {$t < 0} { set t 0 }
    after $t
    ::action::_emit "action_sleep" [dict create \
        level "debug" \
        action "sleep_ms" \
        ms $t \
    ]
}

proc ::action::configure_channel {spawn_id args} {
    if {[llength $args] % 2 != 0} {
        error "action::configure_channel espera pares chave/valor do fconfigure"
    }
    eval [linsert $args 0 fconfigure $spawn_id]
    ::action::_emit "action_configure_channel" [dict create \
        level "debug" \
        spawn_id $spawn_id \
        action "configure_channel" \
        args $args \
    ]
}

