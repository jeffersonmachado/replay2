package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib record.tcl]

test record_session_markers_01 "arquivo registra record_started/record_stopped e filtra tipos" -body {
    set f [file join [::tcltest::configure -tmpdir] record_markers.log]
    catch {file delete -force $f}

    ::record::start $f
    ::record::event_sink [dict create type action_sent keys "abc" ts_ms 1000 pid 1]
    ::record::event_sink [dict create type screen_captured ts_ms 1001 pid 1]
    ::record::stop

    set fh [open $f r]
    set lines {}
    while {[gets $fh line] >= 0} {
        if {[string trim $line] ne ""} { lappend lines $line }
    }
    close $fh

    set types {}
    foreach line $lines {
        set ev [lindex $line 0]
        lappend types [dict get $ev type]
    }
    list [llength $lines] $types
} -result [list 3 [list record_started action_sent record_stopped]]

test record_append_keeps_sessions_01 "append preserva sessões anteriores com novas marcas" -body {
    set f [file join [::tcltest::configure -tmpdir] record_append.log]
    catch {file delete -force $f}

    ::record::start $f
    ::record::stop
    ::record::start $f
    ::record::stop

    set fh [open $f r]
    set n 0
    while {[gets $fh line] >= 0} {
        if {[string trim $line] ne ""} { incr n }
    }
    close $fh
    set n
} -result 4

test record_start_validation_01 "start com path vazio falha; start duplo falha" -body {
    set r1 [catch {::record::start ""} e1]
    set f [file join [::tcltest::configure -tmpdir] record_double.log]
    catch {file delete -force $f}
    ::record::start $f
    set r2 [catch {::record::start $f} e2]
    ::record::stop
    list $r1 $r2
} -result [list 1 1]
