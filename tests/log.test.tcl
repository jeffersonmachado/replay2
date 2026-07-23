package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib log.tcl]

test log_to_json_even_words_01 "string com número par de palavras NÃO vira objeto JSON" -body {
    ::log::_to_json_value "foo bar"
} -result {"foo bar"}

test log_to_json_scalars_01 "inteiros, decimais e booleanos saem sem aspas" -body {
    list \
        [::log::_to_json_value "123"] \
        [::log::_to_json_value "-4.5"] \
        [::log::_to_json_value "true"] \
        [::log::_to_json_value "false"]
} -result [list 123 -4.5 true false]

test log_to_json_dictlike_string_01 "string que parece dict permanece string" -body {
    ::log::_to_json_value {a 1 b 2}
} -result {"a 1 b 2"}

test log_escape_json_controls_01 "controles < 0x20 são escapados como \\u00XX" -body {
    ::log::_escape_json "a\x01\x1fb"
} -result {a\u0001\u001fb}

test log_escape_json_basic_01 "aspas, barra e quebras escapados" -body {
    ::log::_escape_json "a\"b\\c\nd"
} -result {a\"b\\c\nd}

test log_dict_to_json_01 "dict vira objeto JSON com tipos explícitos preservados" -body {
    ::log::_dict_to_json [dict create type unknown_screen ts_ms 123 level "warn"]
} -result {{"type":"unknown_screen","ts_ms":123,"level":"warn"}}

test log_level_enabled_01 "nível filtra severidades abaixo do configurado" -body {
    ::log::configure -level warn
    set r [list [::log::level_enabled debug] [::log::level_enabled info] [::log::level_enabled warn] [::log::level_enabled error]]
    ::log::configure -level info
    set r
} -result [list 0 0 1 1]
