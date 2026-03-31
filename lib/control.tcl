########################################################################
## control.tcl
## Servidor TCP simples para controle da engine (local-only por padrão).
##
## Protocolo (linha a linha):
##   status
##   pause
##   resume
##   step
##   send <texto>
##   dump
##   quit
##
## Observações:
## - Sem autenticação (MVP). Por padrão bind em 127.0.0.1.
## - Escopo previsto: uso local/interno em ambiente controlado.
## - Não exponha esta porta em rede aberta sem camada adicional de proteção
##   (ex.: túnel, firewall, autenticação externa ou bind restrito).
########################################################################

namespace eval ::control {
    namespace export start stop poll update_status should_dispatch drain_actions drain_dump_request should_stop port

    variable cfg
    set cfg [dict create bind "127.0.0.1" port 0 enabled 0]

    variable serverSock ""
    variable actualPort 0
    variable clients
    array set clients {}

    variable paused 0
    variable step_pending 0
    variable stop_requested 0

    variable pendingActions
    set pendingActions {}

    variable dump_requested 0

    variable status
    set status [dict create]

    # Última tela conhecida (para TUI/debug)
    variable last_raw ""
    variable last_norm ""
    variable last_sig ""
    variable last_state ""
}

proc ::control::port {} {
    variable actualPort
    return $actualPort
}

proc ::control::start {bind port} {
    variable serverSock
    variable actualPort
    variable cfg

    if {$serverSock ne ""} { return $actualPort }
    set p [expr {int($port)}]
    if {$p < 0} { set p 0 }
    dict set cfg bind $bind
    dict set cfg port $p
    dict set cfg enabled 1

    set serverSock [socket -server ::control::_on_accept -myaddr $bind $p]
    set actualPort [lindex [fconfigure $serverSock -sockname] 2]
    if {[llength [info procs ::events::emit]]} {
        ::events::emit "control_started" [dict create level "info" bind $bind port $actualPort]
    }
    return $actualPort
}

proc ::control::stop {} {
    variable serverSock
    variable clients
    variable actualPort

    if {$serverSock ne ""} {
        catch {close $serverSock}
        set serverSock ""
        set actualPort 0
    }
    foreach c [array names clients] {
        catch {close $c}
        unset clients($c)
    }
    if {[llength [info procs ::events::emit]]} {
        ::events::emit "control_stopped" [dict create level "info"]
    }
}

proc ::control::_say {chan line} {
    catch {puts $chan $line}
    catch {flush $chan}
}

proc ::control::_escape_oneline {s} {
    # Escapa para manter 1 linha: \\, \n, \r, \t
    set out $s
    regsub -all {\\} $out {\\\\} out
    regsub -all {\n} $out {\\n} out
    regsub -all {\r} $out {\\r} out
    regsub -all {\t} $out {\\t} out
    return $out
}

proc ::control::_on_accept {chan host port} {
    variable clients
    set clients($chan) [dict create host $host port $port]
    fconfigure $chan -buffering line -translation lf -blocking 0
    fileevent $chan readable [list ::control::_on_readable $chan]
    ::control::_say $chan "dakota-replay2 control ready (type: status|pause|resume|step|send <keys>|dump|screen <raw|norm>|quit)"
    if {[llength [info procs ::events::emit]]} {
        ::events::emit "control_client_connected" [dict create level "info" host $host port $port]
    }
}

proc ::control::_drop_client {chan} {
    variable clients
    if {[info exists clients($chan)]} {
        unset clients($chan)
    }
    catch {close $chan}
}

proc ::control::_on_readable {chan} {
    variable paused
    variable step_pending
    variable stop_requested
    variable pendingActions
    variable dump_requested
    variable status
    variable last_raw
    variable last_norm
    variable last_sig
    variable last_state

    if {[eof $chan]} {
        ::control::_drop_client $chan
        return
    }

    set line ""
    if {[gets $chan line] < 0} { return }
    set line [string trim $line]
    if {$line eq ""} { return }

    set cmd [lindex $line 0]
    set rest [string trim [string range $line [string length $cmd] end]]

    switch -exact -- $cmd {
        status {
            ::control::_say $chan $status
        }
        pause {
            set paused 1
            set step_pending 0
            ::control::_say $chan "ok paused"
        }
        resume {
            set paused 0
            set step_pending 0
            ::control::_say $chan "ok resumed"
        }
        step {
            set paused 1
            set step_pending 1
            ::control::_say $chan "ok step"
        }
        send {
            # Enfileira envio manual de teclas
            lappend pendingActions [dict create type send_keys keys $rest]
            ::control::_say $chan "ok queued"
        }
        dump {
            set dump_requested 1
            ::control::_say $chan "ok dump_requested"
        }
        screen {
            set which [string tolower $rest]
            switch -exact -- $which {
                raw {
                    ::control::_say $chan "SCREEN raw [::control::_escape_oneline $last_raw]"
                }
                norm {
                    ::control::_say $chan "SCREEN norm [::control::_escape_oneline $last_norm]"
                }
                default {
                    ::control::_say $chan "error screen_requires_raw_or_norm"
                }
            }
        }
        quit {
            set stop_requested 1
            ::control::_say $chan "ok quitting"
        }
        default {
            ::control::_say $chan "error unknown_command"
        }
    }
}

proc ::control::update_status {d} {
    variable status
    set status $d
}

proc ::control::set_last_screen {raw norm sig state} {
    variable last_raw
    variable last_norm
    variable last_sig
    variable last_state
    set last_raw $raw
    set last_norm $norm
    set last_sig $sig
    set last_state $state
}

proc ::control::poll {} {
    # Nada a fazer: o processamento é por fileevent.
    return
}

proc ::control::should_dispatch {} {
    variable paused
    variable step_pending
    if {!$paused} { return 1 }
    if {$step_pending} {
        set step_pending 0
        return 1
    }
    return 0
}

proc ::control::drain_actions {} {
    variable pendingActions
    set out $pendingActions
    set pendingActions {}
    return $out
}

proc ::control::drain_dump_request {} {
    variable dump_requested
    set v $dump_requested
    set dump_requested 0
    return $v
}

proc ::control::should_stop {} {
    variable stop_requested
    return $stop_requested
}
