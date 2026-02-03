package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib signature.tcl]

proc _read_fixture {name} {
    set testDir [file dirname [file normalize [info script]]]
    set p [file join $testDir fixtures $name]
    set ch [open $p r]
    set data ""
    set rc [catch { set data [read $ch] } err]
    catch { close $ch }
    if {$rc} { error $err }
    return $data
}

test signature_from_screen_login_01 "gera assinatura com TIT e labels (login)" -body {
    set screen [_read_fixture login_screen.txt]
    set sig [::signature::from_screen $screen]
    set sig
} -match regexp -result {^L=\d+;W=\d+;TIT=.*;LBL=.*(Usuário|Usuario).*;.*(Senha|Senha ).*$}

test signature_from_screen_menu_01 "gera assinatura com TIT e label (menu)" -body {
    set screen [_read_fixture menu_screen.txt]
    set sig [::signature::from_screen $screen]
    set sig
} -match regexp -result {^L=\d+;W=\d+;TIT=.*;LBL=.*(Opção|Opcao).*$}

