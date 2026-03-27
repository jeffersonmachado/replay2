########################################################################
## plugins.tcl
## Descoberta/carregamento de handlers de tela (plugins) com enable/disable.
##
## Arquivo de estado (dict Tcl):
##   key: nome do arquivo (basename, ex.: screen_login.tcl)
##   val: 1 (enabled) | 0 (disabled)
##
## Observação:
## - Se o arquivo não existir, todos os plugins são considerados enabled.
########################################################################

namespace eval ::plugins {
    namespace export discover read_state write_state is_enabled load_screens set_enabled
}

proc ::plugins::discover {screens_dir} {
    if {![file isdirectory $screens_dir]} { return {} }
    return [lsort [glob -nocomplain -directory $screens_dir *.tcl]]
}

proc ::plugins::read_state {plugins_file} {
    if {$plugins_file eq ""} { return [dict create] }
    if {![file exists $plugins_file]} { return [dict create] }
    set f [open $plugins_file r]
    set content [read $f]
    close $f
    set content [string trim $content]
    if {$content eq ""} { return [dict create] }
    # Deve ser uma string representando um dict Tcl.
    if {[catch {dict size $content}]} {
        error "plugins::read_state: arquivo inválido (não é dict Tcl): $plugins_file"
    }
    return $content
}

proc ::plugins::write_state {plugins_file stateDict} {
    if {$plugins_file eq ""} { error "plugins::write_state: plugins_file vazio" }
    set dir [file dirname $plugins_file]
    if {$dir ne ""} { file mkdir $dir }
    set f [open $plugins_file w]
    fconfigure $f -translation lf
    puts -nonewline $f $stateDict
    close $f
}

proc ::plugins::is_enabled {stateDict plugin_path} {
    # Decide pelo basename, para portabilidade entre paths.
    set base [file tail $plugin_path]
    if {[dict exists $stateDict $base]} {
        return [expr {int([dict get $stateDict $base]) != 0}]
    }
    return 1
}

proc ::plugins::set_enabled {plugins_file plugin_name enabled} {
    # plugin_name pode ser basename ou caminho.
    set base [file tail $plugin_name]
    set st [::plugins::read_state $plugins_file]
    dict set st $base [expr {int($enabled) != 0}]
    ::plugins::write_state $plugins_file $st
    return $st
}

proc ::plugins::load_screens {screens_dir plugins_file} {
    set files [::plugins::discover $screens_dir]
    set st [::plugins::read_state $plugins_file]

    set loaded {}
    foreach f $files {
        if {![::plugins::is_enabled $st $f]} {
            if {[llength [info procs ::events::emit]]} {
                ::events::emit "plugin_skipped" [dict create level "info" file $f reason "disabled"]
            }
            continue
        }
        if {[catch {source $f} err]} {
            if {[llength [info procs ::events::emit]]} {
                ::events::emit "plugin_load_error" [dict create level "error" file $f error $err]
            }
            puts stderr "Erro ao carregar plugin $f: $err"
            continue
        }
        lappend loaded $f
        if {[llength [info procs ::events::emit]]} {
            ::events::emit "plugin_loaded" [dict create level "info" file $f]
        }
    }
    return $loaded
}

