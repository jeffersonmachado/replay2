########################################################################
## events.tcl
## Barramento simples de eventos (portável, sem dependências externas).
##
## Objetivos:
## - Permitir que o core emita eventos estruturados (dict Tcl)
## - Permitir múltiplos sinks (logger, dumper, recorder, UI)
## - Isolar falhas: sink não pode derrubar a engine
########################################################################

namespace eval ::events {
    namespace export configure register_sink unregister_sink emit now_ms

    # Lista de procs: cada sink recebe 1 argumento (dict do evento)
    variable sinks
    set sinks {}

    # Config simples (extensível)
    variable cfg
    set cfg [dict create enabled 1]
}

proc ::events::configure {args} {
    variable cfg
    if {[llength $args] % 2 != 0} {
        error "events::configure espera pares chave/valor"
    }
    foreach {k v} $args {
        switch -exact -- $k {
            -enabled { dict set cfg enabled [expr {int($v) != 0}] }
            default  { error "events::configure: opção desconhecida: $k" }
        }
    }
    return $cfg
}

proc ::events::register_sink {sinkProc} {
    variable sinks
    if {$sinkProc eq ""} { error "events::register_sink: sinkProc vazio" }
    if {[lsearch -exact $sinks $sinkProc] >= 0} {
        return
    }
    lappend sinks $sinkProc
}

proc ::events::unregister_sink {sinkProc} {
    variable sinks
    set idx [lsearch -exact $sinks $sinkProc]
    if {$idx < 0} { return }
    set sinks [lreplace $sinks $idx $idx]
}

proc ::events::now_ms {} {
    return [clock milliseconds]
}

proc ::events::emit {type payload} {
    variable cfg
    variable sinks

    if {![dict get $cfg enabled]} { return }
    if {$type eq ""} { return }

    # Normaliza payload
    if {$payload eq ""} {
        set payload [dict create]
    } elseif {![string match "dict *" [tcl::unsupported::representation $payload]]} {
        # Tenta aceitar qualquer coisa que "pareça" dict; se falhar, encapsula.
        if {[catch {dict size $payload}]} {
            set payload [dict create value $payload]
        }
    }

    set ev [dict create \
        type $type \
        ts_ms [now_ms] \
        pid [pid] \
    ]

    # Mescla payload por último (payload sobrescreve campos se necessário)
    foreach {k v} $payload {
        dict set ev $k $v
    }

    foreach sink $sinks {
        if {![llength [info procs $sink]]} {
            continue
        }
        catch {
            $sink $ev
        }
    }
}

