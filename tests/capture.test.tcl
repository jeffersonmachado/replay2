package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib capture.tcl]

test capture_apply_boundaries_none_01 "sem boundary retorna o texto original" -body {
    ::capture::apply_screen_boundaries "abc\ndef"
} -result "abc\ndef"

test capture_apply_boundaries_clearscreen_01 {mantém apenas trecho após ESC[2J ESC[H} -body {
    set s "OLD1\nOLD2\033\[2J\033\[HNEW1\nNEW2"
    ::capture::apply_screen_boundaries $s
} -result "NEW1\nNEW2"

test capture_apply_boundaries_inverse_01 {mantém apenas trecho após ESC[H ESC[2J} -body {
    set s "OLD\033\[H\033\[2JNEW"
    ::capture::apply_screen_boundaries $s
} -result "NEW"

