"""A number in a tool reply reads as the JS tools write it: `44%`, not `44.0%`.

The service sends quota percentages as floats. JS parses `44.0` to 44 and prints `44`; Python keeps the
float and printed `44.0` (seen live on comms_usage, 0.9.11 slice 2). The reply fixtures cannot carry it:
the JS recorder rewrites each case file, and JSON.stringify writes 44.0 as 44, so the Python side never
receives the float. Hence this test of the one formatter, on the integral float the fixtures cannot hold.
"""

from service.sse.tool_format import js_number


def test_an_integral_float_prints_as_an_integer():
    assert js_number(44.0) == "44"


def test_a_fraction_and_an_int_print_as_they_are():
    assert (js_number(12.5), js_number(20)) == ("12.5", "20")
