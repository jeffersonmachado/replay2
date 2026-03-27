########################################################################
## log.tcl
## Logger simples com níveis e suporte a JSON-lines (opcional).
##
## Objetivos:
## - Respeitar `--log-level` (debug|info|warn|error)
## - Permitir formato: text|json|both
## - Agir como sink do events::emit (1 argumento: dict evento)
########################################################################

namespace eval ::log {
    namespace export configure level_enabled debug info warn error event_sink

    variable cfg
    set cfg [dict create \
        level "info" \
        format "text" \
        stream "stderr" \
    ]
}

proc ::log::_lvl_to_num {lvl} {
    set l [string tolower $lvl]
    switch -exact -- $l {
        debug { return 10 }
        info  { return 20 }
        warn  { return 30 }
        error { return 40 }
        default { return 20 }
    }
}

proc ::log::configure {args} {
    variable cfg
    if {[llength $args] % 2 != 0} {
        error "log::configure espera pares chave/valor"
    }
    foreach {k v} $args {
        switch -exact -- $k {
            -level {
                dict set cfg level [string tolower $v]
            }
            -format {
                set f [string tolower $v]
                if {$f ni {"text" "json" "both"}} {
                    error "log::configure: -format inválido (use text|json|both)"
                }
                dict set cfg format $f
            }
            -stream {
                # "stdout" ou "stderr"
                if {$v ni {"stdout" "stderr"}} { error "log::configure: -stream inválido" }
                dict set cfg stream $v
            }
            default {
                error "log::configure: opção desconhecida: $k"
            }
        }
    }
    return $cfg
}

proc ::log::level_enabled {lvl} {
    variable cfg
    set want [_lvl_to_num $lvl]
    set cur  [_lvl_to_num [dict get $cfg level]]
    expr {$want >= $cur}
}

proc ::log::_out {line} {
    variable cfg
    if {[dict get $cfg stream] eq "stdout"} {
        puts $line
    } else {
        puts stderr $line
    }
}

proc ::log::_escape_json {s} {
    # Encoder minimalista para strings JSON.
    set out $s
    regsub -all {\\} $out {\\\\} out
    regsub -all {"}  $out {\\"} out
    regsub -all {\n} $out {\\n} out
    regsub -all {\r} $out {\\r} out
    regsub -all {\t} $out {\\t} out
    return $out
}

proc ::log::_to_json_value {v {depth 0}} {
    # Converte escalar/list/dict para JSON (recursivo, com limite).
    if {$depth > 6} {
        return "\"<max_depth>\""
    }

    # dict -> object
    if {![catch {dict size $v}]} {
        return [::log::_dict_to_json $v [expr {$depth + 1}]]
    }

    # lista -> array (heurística: se for lista parseável com len>1)
    if {![catch {set n [llength $v]}] && $n > 1} {
        set arrParts {}
        foreach item $v {
            lappend arrParts [::log::_to_json_value $item [expr {$depth + 1}]]
        }
        return "\[[join $arrParts ,]\]"
    }

    # Decide entre número/bool e string.
    if {[string is integer -strict $v] || [string is double -strict $v]} {
        return $v
    }
    if {$v eq "true" || $v eq "false"} {
        return $v
    }
    return "\"[::log::_escape_json $v]\""
}

proc ::log::_dict_to_json {d {depth 0}} {
    set parts {}
    foreach {k v} $d {
        set key "\"[::log::_escape_json $k]\""
        set val [::log::_to_json_value $v $depth]
        lappend parts "${key}:${val}"
    }
    return "\{[join $parts ,]\}"
}

proc ::log::_event_default_level {evType} {
    # Mapeia tipo de evento -> severidade padrão.
    switch -exact -- $evType {
        unknown_screen { return "warn" }
        capture_error  { return "error" }
        handler_error  { return "error" }
        default        { return "debug" }
    }
}

proc ::log::event_sink {ev} {
    # Sink para ::events::register_sink.
    variable cfg

    set type [dict get $ev type]
    set lvl  [::log::_event_default_level $type]
    if {[dict exists $ev level]} {
        set lvl [string tolower [dict get $ev level]]
    }

    if {![level_enabled $lvl]} { return }

    set fmt [dict get $cfg format]
    if {$fmt in {"text" "both"}} {
        set ts [dict get $ev ts_ms]
        set pid [dict get $ev pid]
        # Evita despejar raw/norm enormes no log-texto; sinaliza tamanhos.
        set compact $ev
        foreach key {raw_screen norm_screen} {
            if {[dict exists $compact $key]} {
                set v [dict get $compact $key]
                dict set compact ${key}_len [string length $v]
                dict unset compact $key
            }
        }
        ::log::_out "[string toupper $lvl] ts_ms=$ts pid=$pid type=$type data=[dict remove $compact type ts_ms pid]"
    }

    if {$fmt in {"json" "both"}} {
        # JSON-lines: preserva campos completos (inclui raw/norm se vierem).
        ::log::_out [::log::_dict_to_json $ev]
    }
}

proc ::log::debug {msg {fields ""}} {
    if {![level_enabled "debug"]} { return }
    set ev [dict create type log level debug message $msg]
    if {$fields ne ""} { foreach {k v} $fields { dict set ev $k $v } }
    event_sink $ev
}

proc ::log::info {msg {fields ""}} {
    if {![level_enabled "info"]} { return }
    set ev [dict create type log level info message $msg]
    if {$fields ne ""} { foreach {k v} $fields { dict set ev $k $v } }
    event_sink $ev
}

proc ::log::warn {msg {fields ""}} {
    if {![level_enabled "warn"]} { return }
    set ev [dict create type log level warn message $msg]
    if {$fields ne ""} { foreach {k v} $fields { dict set ev $k $v } }
    event_sink $ev
}

proc ::log::error {msg {fields ""}} {
    if {![level_enabled "error"]} { return }
    set ev [dict create type log level error message $msg]
    if {$fields ne ""} { foreach {k v} $fields { dict set ev $k $v } }
    event_sink $ev
}

