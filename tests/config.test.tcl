package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib config.tcl]

proc _get {d k} { dict get $d $k }

test config_parse_defaults_01 "defaults são consistentes" -body {
    set cfg [::config::parse_argv {} $rootDir]
    set expectedTerm "xterm"
    if {[info exists ::env(TERM)] && $::env(TERM) ne ""} {
        set expectedTerm $::env(TERM)
    }
    list \
        [_get $cfg encoding] \
        [_get $cfg rows] \
        [_get $cfg cols] \
        [expr {[_get $cfg term] eq $expectedTerm}] \
        [_get $cfg translation] \
        [_get $cfg capture_timeout] \
        [_get $cfg capture_quiet_ms] \
        [_get $cfg stable_required] \
        [_get $cfg max_bytes] \
        [llength [_get $cfg legacy_cmd]]
} -result [list "utf-8" 25 80 1 "crlf" 2.0 200 1 65535 0]

test config_parse_overrides_01 "overrides via argv funcionam" -body {
    set cfg [::config::parse_argv {--encoding cp850 --rows 30 --cols 132 --term xterm-256color --translation lf --capture-timeout 3.5 --stable-required 2 --max-bytes 1234 --dump-on-unknown 0 --log-level DEBUG} $rootDir]
    list \
        [_get $cfg encoding] \
        [_get $cfg rows] \
        [_get $cfg cols] \
        [_get $cfg term] \
        [_get $cfg geometry_source] \
        [_get $cfg translation] \
        [_get $cfg capture_timeout] \
        [_get $cfg stable_required] \
        [_get $cfg max_bytes] \
        [_get $cfg dump_on_unknown] \
        [_get $cfg log_level]
} -result [list "cp850" 30 132 "xterm-256color" "explicit" "lf" 3.5 2 1234 0 "debug"]

test config_reject_invalid_geometry_01 "geometria invalida rejeitada" -body {
    catch {::config::parse_argv {--rows 2.5 --cols 80} $rootDir} err
    expr {[string match "*Geometria inválida*" $err]}
} -result 1

test config_env_override_encoding_01 "override via env DAKOTA_ENCODING" -body {
    set old ""
    set had 0
    if {[info exists ::env(DAKOTA_ENCODING)]} {
        set old $::env(DAKOTA_ENCODING)
        set had 1
    }

    set ::env(DAKOTA_ENCODING) "latin1"
    set cfg [::config::parse_argv {} $rootDir]
    set got [_get $cfg encoding]

    if {$had} {
        set ::env(DAKOTA_ENCODING) $old
    } else {
        catch {unset ::env(DAKOTA_ENCODING)}
    }
    set got
} -result "latin1"

test config_find_tclsh_01 "find_tclsh retorna um candidato não vazio" -body {
    set t [::config::find_tclsh]
    expr {$t ne ""}
} -result 1
