package require tcltest
namespace import ::tcltest::*

set testDir [file dirname [file normalize [info script]]]
set rootDir [file normalize [file join $testDir ..]]

source [file join $rootDir lib normalize.tcl]

test normalize_strip_ansi_01 "remove sequências ANSI comuns" -body {
    set s "A\033\[2J\033\[HBC\033\[31mRED\033\[0mD"
    ::normalize::strip_ansi $s
} -result "ABCREDD"

test normalize_boxes_01 "normaliza box-drawing unicode para ASCII" -body {
    set s "╔══╗\n║X ║\n╚══╝"
    ::normalize::normalize_boxes $s
} -result "+==+\n|X |\n+==+"

test normalize_whitespace_01 "normaliza CRLF/CR, trim à direita e colapsa vazios" -body {
    set s "a  \r\n\r\n\r\nb\t \r\n\r\nc\r"
    ::normalize::normalize_whitespace $s
} -result "a\n\nb\n\nc\n"

test normalize_screen_pipeline_01 "pipeline completo (ansi + boxes + whitespace)" -body {
    set s "\033\[2J╔═╗ \r\n║a║\r\n╚═╝\r\n"
    ::normalize::screen $s
} -result "+=+\n|a|\n+=+\n"

