package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib events.tcl]

# Captura de eventos emitidos (sink de teste)
namespace eval ::events_test {
    variable received {}
}
proc ::events_test::sink {ev} {
    variable received
    lappend received $ev
}
proc ::events_test::reset {} {
    variable received
    set received {}
}
proc ::events_test::all {} {
    variable received
    return $received
}

test events_register_sink_01 "sink é registrado uma única vez (dedup)" -body {
    ::events_test::reset
    ::events::register_sink ::events_test::sink
    ::events::register_sink ::events_test::sink
    ::events::emit "dedup_test" [dict create]
    set n [llength [::events_test::all]]
    ::events_test::reset
    set n
} -result 1

test events_register_sink_02 "sink vazio é erro" -body {
    catch {::events::register_sink ""} msg
    set msg
} -result {events::register_sink: sinkProc vazio}

test events_emit_01 "evento carrega type, ts_ms e pid; payload é mesclado" -body {
    ::events_test::reset
    ::events::emit "minha_acao" [dict create nivel info detalhe abc]
    set evs [::events_test::all]
    ::events_test::reset
    if {[llength $evs] != 1} { return "eventos=[llength $evs]" }
    set ev [lindex $evs 0]
    list \
        [dict get $ev type] \
        [dict get $ev nivel] \
        [dict get $ev detalhe] \
        [expr {[dict get $ev ts_ms] > 0}] \
        [expr {[dict get $ev pid] == [pid]}]
} -result [list minha_acao info abc 1 1]

test events_emit_02 "payload não-dict é encapsulado em 'value'" -body {
    ::events_test::reset
    ::events::emit "texto_solto" "payload solto aqui"
    set ev [lindex [::events_test::all] 0]
    ::events_test::reset
    dict get $ev value
} -result {payload solto aqui}

test events_emit_03 "payload vazio vira dict vazio (evento só com envelope)" -body {
    ::events_test::reset
    ::events::emit "sem_payload" ""
    set ev [lindex [::events_test::all] 0]
    ::events_test::reset
    list [dict get $ev type] [dict exists $ev value]
} -result [list sem_payload 0]

test events_emit_04 "type vazio não emite" -body {
    ::events_test::reset
    ::events::emit "" [dict create a 1]
    ::events_test::all
} -cleanup {
    ::events_test::reset
} -result {}

test events_emit_05 "barramento desabilitado não entrega eventos" -body {
    ::events_test::reset
    dict set ::events::cfg enabled 0
    ::events::emit "off_test" [dict create]
    set got [::events_test::all]
    dict set ::events::cfg enabled 1
    ::events_test::reset
    set got
} -result {}

test events_emit_06 "sink inexistente é ignorado sem derrubar a emissão" -body {
    ::events_test::reset
    ::events::register_sink ::nao_existe_este_sink
    ::events::emit "robustez" [dict create]
    set got [::events_test::all]
    ::events_test::reset
    llength $got
} -result 1

test events_emit_07 "sink que falha não derruba a engine nem os demais sinks" -body {
    proc ::events_test::sink_ruim {ev} { error "falha proposital" }
    ::events_test::reset
    ::events::register_sink ::events_test::sink_ruim
    ::events::emit "com_sink_ruim" [dict create]
    set got [::events_test::all]
    ::events_test::reset
    rename ::events_test::sink_ruim ""
    # o sink bom ainda recebeu exatamente 1 evento
    llength $got
} -result 1

test events_emit_08 "payload sobrescreve campos do envelope quando necessário" -body {
    ::events_test::reset
    ::events::emit "merge_test" [dict create custom 42]
    set ev [lindex [::events_test::all] 0]
    ::events_test::reset
    dict get $ev custom
} -result 42

test events_now_ms_01 "now_ms retorna inteiro positivo" -body {
    expr {[::events::now_ms] > 0}
} -result 1
