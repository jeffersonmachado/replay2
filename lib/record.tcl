########################################################################
## record.tcl
## Grava eventos em arquivo (formato: 1 dict por linha, encapsulado em list).
##
## Objetivos:
## - Permitir `replay2 record` e inspeção posterior sem libs externas
## - Ser robusto: cada linha é um Tcl list com 1 elemento (o dict)
########################################################################

namespace eval ::record {
    namespace export start stop enabled event_sink

    variable fh ""
    variable path ""
    variable types
    set types {signature_computed route_decision handler_called action_sent action_sleep unknown_screen}
}

proc ::record::enabled {} {
    variable fh
    expr {$fh ne ""}
}

proc ::record::start {filePath} {
    variable fh
    variable path
    if {$filePath eq ""} { error "record::start: path vazio" }
    if {$fh ne ""} { error "record::start: já está gravando" }
    set dir [file dirname $filePath]
    if {$dir ne ""} { file mkdir $dir }
    set fh [open $filePath a]
    fconfigure $fh -translation lf -buffering line
    set path $filePath
    if {[llength [info procs ::events::emit]]} {
        ::events::emit "record_started" [dict create level "info" path $filePath]
    }
}

proc ::record::stop {} {
    variable fh
    variable path
    if {$fh eq ""} { return }
    catch {close $fh}
    set fh ""
    set old $path
    set path ""
    if {[llength [info procs ::events::emit]]} {
        ::events::emit "record_stopped" [dict create level "info" path $old]
    }
}

proc ::record::event_sink {ev} {
    variable fh
    variable types
    if {$fh eq ""} { return }
    set t [dict get $ev type]
    if {[lsearch -exact $types $t] < 0} { return }

    # Uma linha por evento, encapsulado em list (parse fácil e seguro).
    puts $fh [list $ev]
}

