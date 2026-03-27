########################################################################
## config.tcl
## Camada simples de configuração/CLI para a engine.
##
## Objetivos:
## - Permitir trocar comando do legado (local), encoding, timeouts, etc.
## - Manter defaults seguros e compatíveis com o exemplo atual.
## - Não depender de libs externas.
########################################################################

namespace eval ::config {
    namespace export parse_argv usage
}

proc ::config::_parse_legacy_cmd {raw} {
    # Aceita:
    # - lista Tcl (ex.: "ssh user@host legacy_app")
    # - string entre chaves (ex.: "{ssh user@host legacy_app}") => remove chaves
    # - string simples (fallback)
    set s [string trim $raw]
    if {[string length $s] >= 2 && [string index $s 0] eq "{" && [string index $s end] eq "}"} {
        # Se o usuário passou chaves para o shell, removemos a camada externa.
        set s [string range $s 1 end-1]
        set s [string trim $s]
    }
    # Interpretamos como lista Tcl (se tiver espaços vira múltiplos elementos).
    if {[catch {set cmd [lrange $s 0 end]}]} {
        return [list $raw]
    }
    return $cmd
}

proc ::config::find_tclsh {} {
    # Descobre um tclsh executável no PATH (portável entre Linux e AIX).
    # Permite override via env DAKOTA_TCLSH.
    if {[info exists ::env(DAKOTA_TCLSH)] && $::env(DAKOTA_TCLSH) ne ""} {
        return $::env(DAKOTA_TCLSH)
    }

    foreach cand {tclsh tclsh8.6 tclsh86 tclsh8.5 tclsh85} {
        set exe [auto_execok $cand]
        if {$exe ne ""} { return $cand }
    }

    # Fallback para ambientes onde Tcl vem em local fixo.
    foreach cand {/usr/bin/tclsh /opt/freeware/bin/tclsh} {
        if {[file executable $cand]} { return $cand }
    }

    # Último recurso: devolve tclsh e deixa o spawn falhar com erro claro.
    return "tclsh"
}

proc ::config::usage {} {
    return [string trim {
Uso:
  expect bin/main.exp [opções]

Opções:
  --legacy-cmd <tcl_list>     Comando do legado como lista Tcl.
                             Ex: --legacy-cmd "{ssh user@host legacy_app}"
  --encoding <enc>            Encoding (default: utf-8)
  --translation <mode>        Translation (default: crlf)
  --capture-timeout <secs>    Timeout total de captura (default: 2.0)
  --capture-quiet-ms <ms>     Silêncio para considerar captura estável (default: 200)
  --stable-required <n>       Iterações estáveis antes de despachar (default: 1)
  --max-bytes <n>             Limite de bytes por snapshot (default: 65535)
  --screens-dir <path>        Diretório de handlers (default: <app_root>/screens)
  --plugins-file <path>       Arquivo dict de enable/disable de plugins
                             (default: <screens-dir>/plugins.tcldict.txt)
  --dump-dir <path>           Diretório para dumps (default: vazio=desligado)
  --dump-on-unknown <0|1>     Dump quando tela não reconhecida (default: 1)
  --log-level <lvl>           debug|info|warn|error (default: info)
  --log-format <fmt>          text|json|both (default: text)
  --log-stream <s>            stdout|stderr (default: stderr)
  --record-file <path>        Grava eventos em arquivo (default: vazio=desligado)
  --control-port <n>          Porta TCP local p/ controle (0=desliga, default: 0)
  --control-bind <ip>         Bind do servidor de controle (default: 127.0.0.1)
  --help                      Mostra esta ajuda

Env vars (alternativas):
  DAKOTA_LEGACY_CMD, DAKOTA_ENCODING, DAKOTA_LOG_LEVEL, DAKOTA_LOG_FORMAT,
  DAKOTA_LOG_STREAM, DAKOTA_DUMP_DIR, DAKOTA_SCREENS_DIR, DAKOTA_PLUGINS_FILE,
  DAKOTA_RECORD_FILE, DAKOTA_CONTROL_PORT, DAKOTA_CONTROL_BIND
}]\n
}

proc ::config::parse_argv {argv app_root} {
    set default_screens_dir [file join $app_root screens]
    set default_plugins_file [file join $default_screens_dir plugins.tcldict.txt]

    # Defaults
    set cfg [dict create \
        legacy_cmd {} \
        encoding "utf-8" \
        translation "crlf" \
        capture_timeout 2.0 \
        capture_quiet_ms 200 \
        stable_required 1 \
        max_bytes 65535 \
        screens_dir $default_screens_dir \
        plugins_file $default_plugins_file \
        dump_dir "" \
        dump_on_unknown 1 \
        log_level "info" \
        log_format "text" \
        log_stream "stderr" \
        record_file "" \
        control_port 0 \
        control_bind "127.0.0.1" \
    ]

    # Overrides por env
    if {[info exists ::env(DAKOTA_LEGACY_CMD)] && $::env(DAKOTA_LEGACY_CMD) ne ""} {
        dict set cfg legacy_cmd [::config::_parse_legacy_cmd $::env(DAKOTA_LEGACY_CMD)]
    }
    if {[info exists ::env(DAKOTA_ENCODING)] && $::env(DAKOTA_ENCODING) ne ""} {
        dict set cfg encoding $::env(DAKOTA_ENCODING)
    }
    if {[info exists ::env(DAKOTA_LOG_LEVEL)] && $::env(DAKOTA_LOG_LEVEL) ne ""} {
        dict set cfg log_level $::env(DAKOTA_LOG_LEVEL)
    }
    if {[info exists ::env(DAKOTA_LOG_FORMAT)] && $::env(DAKOTA_LOG_FORMAT) ne ""} {
        dict set cfg log_format [string tolower $::env(DAKOTA_LOG_FORMAT)]
    }
    if {[info exists ::env(DAKOTA_LOG_STREAM)] && $::env(DAKOTA_LOG_STREAM) ne ""} {
        dict set cfg log_stream [string tolower $::env(DAKOTA_LOG_STREAM)]
    }
    if {[info exists ::env(DAKOTA_DUMP_DIR)] && $::env(DAKOTA_DUMP_DIR) ne ""} {
        dict set cfg dump_dir $::env(DAKOTA_DUMP_DIR)
    }
    if {[info exists ::env(DAKOTA_SCREENS_DIR)] && $::env(DAKOTA_SCREENS_DIR) ne ""} {
        dict set cfg screens_dir $::env(DAKOTA_SCREENS_DIR)
    }
    if {[info exists ::env(DAKOTA_PLUGINS_FILE)] && $::env(DAKOTA_PLUGINS_FILE) ne ""} {
        dict set cfg plugins_file $::env(DAKOTA_PLUGINS_FILE)
    }
    if {[info exists ::env(DAKOTA_RECORD_FILE)] && $::env(DAKOTA_RECORD_FILE) ne ""} {
        dict set cfg record_file $::env(DAKOTA_RECORD_FILE)
    }
    if {[info exists ::env(DAKOTA_CONTROL_PORT)] && $::env(DAKOTA_CONTROL_PORT) ne ""} {
        dict set cfg control_port [expr {int($::env(DAKOTA_CONTROL_PORT))}]
    }
    if {[info exists ::env(DAKOTA_CONTROL_BIND)] && $::env(DAKOTA_CONTROL_BIND) ne ""} {
        dict set cfg control_bind $::env(DAKOTA_CONTROL_BIND)
    }

    # Parse de argv
    set i 0
    while {$i < [llength $argv]} {
        set a [lindex $argv $i]
        switch -exact -- $a {
            --help {
                puts [usage]
                exit 0
            }
            --legacy-cmd {
                incr i
                if {$i >= [llength $argv]} { error "Falta valor para --legacy-cmd" }
                set raw [lindex $argv $i]
                dict set cfg legacy_cmd [::config::_parse_legacy_cmd $raw]
            }
            --encoding {
                incr i
                dict set cfg encoding [lindex $argv $i]
            }
            --translation {
                incr i
                dict set cfg translation [lindex $argv $i]
            }
            --capture-timeout {
                incr i
                dict set cfg capture_timeout [expr {double([lindex $argv $i])}]
            }
            --capture-quiet-ms {
                incr i
                dict set cfg capture_quiet_ms [expr {int([lindex $argv $i])}]
            }
            --stable-required {
                incr i
                dict set cfg stable_required [expr {int([lindex $argv $i])}]
            }
            --max-bytes {
                incr i
                dict set cfg max_bytes [expr {int([lindex $argv $i])}]
            }
            --screens-dir {
                incr i
                dict set cfg screens_dir [lindex $argv $i]
            }
            --plugins-file {
                incr i
                dict set cfg plugins_file [lindex $argv $i]
            }
            --dump-dir {
                incr i
                dict set cfg dump_dir [lindex $argv $i]
            }
            --dump-on-unknown {
                incr i
                dict set cfg dump_on_unknown [expr {int([lindex $argv $i])}]
            }
            --log-level {
                incr i
                dict set cfg log_level [string tolower [lindex $argv $i]]
            }
            --log-format {
                incr i
                dict set cfg log_format [string tolower [lindex $argv $i]]
            }
            --log-stream {
                incr i
                dict set cfg log_stream [string tolower [lindex $argv $i]]
            }
            --record-file {
                incr i
                dict set cfg record_file [lindex $argv $i]
            }
            --control-port {
                incr i
                dict set cfg control_port [expr {int([lindex $argv $i])}]
            }
            --control-bind {
                incr i
                dict set cfg control_bind [lindex $argv $i]
            }
            default {
                error "Argumento desconhecido: $a\n\n[usage]"
            }
        }
        incr i
    }

    return $cfg
}

