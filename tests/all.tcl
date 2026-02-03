#!/usr/bin/env tclsh
#
# Runner de testes (tcltest) - compatível com Linux e AIX
#

proc _tests_root_dir {} {
    set here [file dirname [file normalize [info script]]]
    return [file normalize [file join $here ..]]
}

set testDir [file dirname [file normalize [info script]]]
set rootDir [_tests_root_dir]

if {[catch {package require tcltest} err]} {
    puts stderr "Erro: pacote 'tcltest' não encontrado: $err"
    puts stderr "Dica: instale Tcl completo (incluindo tcltest) e rode: tclsh tests/all.tcl"
    exit 2
}

namespace import ::tcltest::*

::tcltest::configure \
    -testdir $testDir \
    -tmpdir [file join $testDir tmp] \
    -file *.test.tcl \
    -singleproc 1 \
    -verbose start

file mkdir [::tcltest::configure -tmpdir]

# `runAllTests` retorna 0 quando tudo passa e 1 quando há falhas.
set code [::tcltest::runAllTests]
::tcltest::cleanupTests
exit $code

