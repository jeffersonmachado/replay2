#!/usr/bin/env tclsh
#
# run_tests.tcl — wrapper canônico da suíte Tcl (delega para all.tcl/tcltest)
#
# Uso: tclsh tests/run_tests.tcl
# O exit code de all.tcl (0 = tudo passou, 1 = falhas, 2 = tcltest ausente)
# é propagado diretamente.
#

set testDir [file dirname [file normalize [info script]]]
source [file join $testDir all.tcl]
