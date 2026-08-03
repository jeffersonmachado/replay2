package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib events.tcl]
source [file join $rootDir lib action.tcl]

# Captura de eventos emitidos pelo ::action
namespace eval ::action_test {
    variable received {}
}
proc ::action_test::sink {ev} {
    variable received
    lappend received $ev
}
proc ::action_test::reset {} {
    variable received
    set received {}
}
proc ::action_test::all {} {
    variable received
    return $received
}
::events::register_sink ::action_test::sink

test action_send_keys_01 "sem Expect (comando 'send' indisponível) é erro claro" -body {
    catch {::action::send_keys 42 "abc"} msg
    set msg
} -result {action::send_keys requer Expect (comando 'send' indisponível)}

test action_sleep_ms_01 "duração negativa é clampada para 0 e emite evento" -body {
    ::action_test::reset
    set t0 [clock milliseconds]
    ::action::sleep_ms -50
    set elapsed [expr {[clock milliseconds] - $t0}]
    set evs [::action_test::all]
    ::action_test::reset
    if {[llength $evs] != 1} { return "eventos=[llength $evs]" }
    set ev [lindex $evs 0]
    list \
        [expr {$elapsed < 500}] \
        [dict get $ev type] \
        [dict get $ev action] \
        [dict get $ev ms]
} -result [list 1 action_sleep sleep_ms 0]

test action_sleep_ms_02 "duração válida aguarda e emite ms inteiro" -body {
    ::action_test::reset
    set t0 [clock milliseconds]
    ::action::sleep_ms 30
    set elapsed [expr {[clock milliseconds] - $t0}]
    set ev [lindex [::action_test::all] 0]
    ::action_test::reset
    list \
        [expr {$elapsed >= 25}] \
        [dict get $ev ms]
} -result [list 1 30]

test action_configure_channel_01 "número ímpar de pares é erro" -body {
    catch {::action::configure_channel ch1 -translation} msg
    set msg
} -result {action::configure_channel espera pares chave/valor do fconfigure}

test action_configure_channel_02 "aplica fconfigure no canal e emite evento" -body {
    ::action_test::reset
    set f [file join [::tcltest::configure -tmpdir] action_chan.txt]
    set ch [open $f w+]
    ::action::configure_channel $ch -translation lf -buffering line
    set tr [fconfigure $ch -translation]
    set buf [fconfigure $ch -buffering]
    close $ch
    set ev [lindex [::action_test::all] 0]
    ::action_test::reset
    list \
        [dict get $ev type] \
        [dict get $ev action] \
        $tr \
        $buf
} -result [list action_configure_channel configure_channel {lf lf} line]
