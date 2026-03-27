########################################################################
## dump.tcl
## Dump de diagnósticos (raw/norm/signature/estado) para debug/auditoria.
##
## Objetivos:
## - Implementar `--dump-dir` e `--dump-on-unknown`
## - Gerar artefatos fáceis de inspecionar e versionar
## - Ser portável (Linux + AIX), sem libs externas
########################################################################

namespace eval ::dump {
    namespace export configure enabled dump_unknown event_sink

    variable cfg
    set cfg [dict create \
        dir "" \
        on_unknown 1 \
        enabled 0 \
    ]

    variable seq
    set seq 0
}

proc ::dump::configure {args} {
    variable cfg
    if {[llength $args] % 2 != 0} {
        error "dump::configure espera pares chave/valor"
    }
    foreach {k v} $args {
        switch -exact -- $k {
            -dir {
                dict set cfg dir $v
                dict set cfg enabled [expr {$v ne ""}]
            }
            -on_unknown {
                dict set cfg on_unknown [expr {int($v) != 0}]
            }
            -enabled {
                dict set cfg enabled [expr {int($v) != 0}]
            }
            default {
                error "dump::configure: opção desconhecida: $k"
            }
        }
    }
    return $cfg
}

proc ::dump::enabled {} {
    variable cfg
    expr {[dict get $cfg enabled] && [dict get $cfg dir] ne ""}
}

proc ::dump::_safe_filename {s} {
    # Substitui caracteres potencialmente problemáticos no nome do arquivo.
    set out $s
    regsub -all {[^[:alnum:]\.\-\_]+} $out "_" out
    if {[string length $out] > 80} {
        set out [string range $out 0 79]
    }
    if {$out eq ""} { set out "dump" }
    return $out
}

proc ::dump::_write_file {path content {binary 0}} {
    if {$binary} {
        set f [open $path "wb"]
        fconfigure $f -translation binary
    } else {
        set f [open $path "w"]
        fconfigure $f -translation lf
    }
    puts -nonewline $f $content
    close $f
}

proc ::dump::dump_unknown {ev} {
    variable cfg
    variable seq

    if {![enabled]} { return "" }
    if {![dict get $cfg on_unknown]} { return "" }

    set baseDir [dict get $cfg dir]
    file mkdir $baseDir

    set ts_ms [dict get $ev ts_ms]
    incr seq

    set sig "unknown"
    if {[dict exists $ev signature]} { set sig [dict get $ev signature] }
    set sigPart [::dump::_safe_filename $sig]

    set folder [file join $baseDir "unknown_${ts_ms}_${seq}_${sigPart}"]
    file mkdir $folder

    # Conteúdos grandes vão em arquivos separados
    if {[dict exists $ev raw_screen]} {
        ::dump::_write_file [file join $folder "raw_screen.txt"] [dict get $ev raw_screen]
    }
    if {[dict exists $ev norm_screen]} {
        ::dump::_write_file [file join $folder "norm_screen.txt"] [dict get $ev norm_screen]
    }

    # Metadados em dict Tcl (fácil de `source`/`dict get`)
    set meta [dict create]
    foreach k {type ts_ms pid signature state stable_required stable_count last_sig} {
        if {[dict exists $ev $k]} { dict set meta $k [dict get $ev $k] }
    }
    if {[dict exists $ev dump_reason]} { dict set meta dump_reason [dict get $ev dump_reason] }
    if {[dict exists $ev handler_candidates]} { dict set meta handler_candidates [dict get $ev handler_candidates] }

    # Inclui tamanhos (ajuda sem abrir os arquivos)
    if {[dict exists $ev raw_screen]}  { dict set meta raw_len  [string length [dict get $ev raw_screen]] }
    if {[dict exists $ev norm_screen]} { dict set meta norm_len [string length [dict get $ev norm_screen]] }

    ::dump::_write_file [file join $folder "meta.tcldict.txt"] $meta

    return $folder
}

proc ::dump::event_sink {ev} {
    # Sink para ::events::register_sink.
    set type [dict get $ev type]
    if {$type eq "unknown_screen"} {
        catch { ::dump::dump_unknown $ev }
    }
}

