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
  --dump-dir <path>           Diretório para dumps (default: vazio=desligado)
  --dump-on-unknown <0|1>     Dump quando tela não reconhecida (default: 1)
  --log-level <lvl>           debug|info|warn|error (default: info)
  --help                      Mostra esta ajuda

Env vars (alternativas):
  DAKOTA_LEGACY_CMD, DAKOTA_ENCODING, DAKOTA_LOG_LEVEL, DAKOTA_DUMP_DIR
}]\n
}

proc ::config::parse_argv {argv app_root} {
    # Defaults
    set cfg [dict create \
        legacy_cmd {} \
        encoding "utf-8" \
        translation "crlf" \
        capture_timeout 2.0 \
        capture_quiet_ms 200 \
        stable_required 1 \
        max_bytes 65535 \
        dump_dir "" \
        dump_on_unknown 1 \
        log_level "info" \
    ]

    # Overrides por env
    if {[info exists ::env(DAKOTA_LEGACY_CMD)] && $::env(DAKOTA_LEGACY_CMD) ne ""} {
        if {[catch {set cmd [lrange $::env(DAKOTA_LEGACY_CMD) 0 end]}]} {
            # Se não for uma lista Tcl válida, deixa como string única
            set cmd [list $::env(DAKOTA_LEGACY_CMD)]
        }
        dict set cfg legacy_cmd $cmd
    }
    if {[info exists ::env(DAKOTA_ENCODING)] && $::env(DAKOTA_ENCODING) ne ""} {
        dict set cfg encoding $::env(DAKOTA_ENCODING)
    }
    if {[info exists ::env(DAKOTA_LOG_LEVEL)] && $::env(DAKOTA_LOG_LEVEL) ne ""} {
        dict set cfg log_level $::env(DAKOTA_LOG_LEVEL)
    }
    if {[info exists ::env(DAKOTA_DUMP_DIR)] && $::env(DAKOTA_DUMP_DIR) ne ""} {
        dict set cfg dump_dir $::env(DAKOTA_DUMP_DIR)
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
                # Espera uma lista Tcl válida (entre aspas/chaves no shell)
                set cmd [lrange $raw 0 end]
                dict set cfg legacy_cmd $cmd
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
            default {
                error "Argumento desconhecido: $a\n\n[usage]"
            }
        }
        incr i
    }

    return $cfg
}

