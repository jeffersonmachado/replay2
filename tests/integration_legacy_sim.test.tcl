package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib config.tcl]
source [file join $rootDir lib normalize.tcl]
source [file join $rootDir lib signature.tcl]

set hasExpect 1
if {[catch {package require Expect} err]} {
    set hasExpect 0
}
::tcltest::testConstraint expect $hasExpect

test integration_legacy_sim_login_01 "simulador gera tela de login reconhecível" -constraints expect -body {
    set tclsh [::config::find_tclsh]
    set sim [file join $rootDir examples legacy_sim.tcl]

    # Spawn do simulador
    log_user 0
    spawn -nottycopy -nottyinit $tclsh $sim

    # Coleta um pouco de saída inicial (tela de login)
    set raw ""
    # Evita dependência de regex do Expect (varia por build): lê direto do pty.
    fconfigure $spawn_id -blocking 0 -buffering none
    set deadline [expr {[clock milliseconds] + 800}]
    while {[clock milliseconds] < $deadline} {
        append raw [read $spawn_id]
        after 20
    }

    # Normaliza e assina
    set norm [::normalize::screen $raw]
    set sig  [::signature::from_screen $norm]

    # Limpa processo
    catch {close}
    catch {wait}

    set sig
} -match regexp -result {TIT=.*;LBL=.*(Usuário|Usuario).*;.*(Senha|Senha ).*}

