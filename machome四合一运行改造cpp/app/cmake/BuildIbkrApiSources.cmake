# Build the user-supplied official IBKR C++ client sources without copying them
# into this repository. Newer SDKs include generated protobuf sources and use
# Intel BID decimal entry points that are not shipped on every macOS setup.
function(machome_configure_ibkr_api_sources out_sources out_link_protobuf out_extra_libraries client_dir protobuf_dir)
    if(NOT EXISTS "${client_dir}/EClientSocket.cpp")
        message(FATAL_ERROR "IBKR C++ API source build requires EClientSocket.cpp in ${client_dir}")
    endif()

    find_package(Protobuf REQUIRED)
    find_package(absl CONFIG QUIET)
    file(GLOB MACHOME_IBKR_ROOT_SOURCES CONFIGURE_DEPENDS "${client_dir}/*.cpp")

    set(MACHOME_IBKR_PROTO_SOURCES "")
    if(protobuf_dir)
        file(GLOB MACHOME_IBKR_PROTO_SOURCES CONFIGURE_DEPENDS
            "${protobuf_dir}/*.cc"
            "${protobuf_dir}/*.cpp")
    endif()

    # IBKR's Decimal wrapper receives values from text/protobuf and exposes the
    # opaque 64-bit Decimal type. This compatibility shim keeps that round-trip
    # self-consistent on macOS builds where Intel's libbid is unavailable. It is
    # intentionally private to the bridge and never appears in the wire contract.
    set(MACHOME_IBKR_BID_SHIM "${CMAKE_CURRENT_BINARY_DIR}/machome_ibkr_bid_decimal_shim.cpp")
    file(WRITE "${MACHOME_IBKR_BID_SHIM}" [=[
#include <climits>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>

using Decimal = unsigned long long;

namespace {
Decimal fromDouble(double value)
{
    Decimal out = 0;
    static_assert(sizeof(out) == sizeof(value), "Decimal shim expects 64-bit double");
    std::memcpy(&out, &value, sizeof(out));
    return out;
}

double toDouble(Decimal value)
{
    if (value == ULLONG_MAX) return std::numeric_limits<double>::quiet_NaN();
    double out = 0.0;
    std::memcpy(&out, &value, sizeof(out));
    return out;
}
}

extern "C" Decimal __bid64_add(Decimal a, Decimal b, unsigned int, unsigned int *flags)
{ if (flags) *flags = 0; return fromDouble(toDouble(a) + toDouble(b)); }
extern "C" Decimal __bid64_sub(Decimal a, Decimal b, unsigned int, unsigned int *flags)
{ if (flags) *flags = 0; return fromDouble(toDouble(a) - toDouble(b)); }
extern "C" Decimal __bid64_mul(Decimal a, Decimal b, unsigned int, unsigned int *flags)
{ if (flags) *flags = 0; return fromDouble(toDouble(a) * toDouble(b)); }
extern "C" Decimal __bid64_div(Decimal a, Decimal b, unsigned int, unsigned int *flags)
{ if (flags) *flags = 0; return fromDouble(toDouble(a) / toDouble(b)); }
extern "C" Decimal __bid64_from_string(char *text, unsigned int, unsigned int *flags)
{
    if (flags) *flags = 0;
    if (!text || !*text) return ULLONG_MAX;
    return fromDouble(std::strtod(text, nullptr));
}
extern "C" void __bid64_to_string(char *out, Decimal value, unsigned int *flags)
{
    if (flags) *flags = 0;
    if (!out) return;
    if (value == ULLONG_MAX) { out[0] = '\0'; return; }
    const double numeric = toDouble(value);
    if (std::isnan(numeric)) { std::snprintf(out, 64, "+NaN"); return; }
    std::snprintf(out, 64, "%.15g", numeric);
}
extern "C" double __bid64_to_binary64(Decimal value, unsigned int, unsigned int *flags)
{ if (flags) *flags = 0; return toDouble(value); }
extern "C" Decimal __binary64_to_bid64(double value, unsigned int, unsigned int *flags)
{ if (flags) *flags = 0; return fromDouble(value); }
]=])

    set(${out_sources}
        ${MACHOME_IBKR_ROOT_SOURCES}
        ${MACHOME_IBKR_PROTO_SOURCES}
        "${MACHOME_IBKR_BID_SHIM}"
        PARENT_SCOPE)
    set(${out_link_protobuf} ON PARENT_SCOPE)
    set(MACHOME_IBKR_EXTRA_LIBRARIES "")
    if(absl_FOUND)
        list(APPEND MACHOME_IBKR_EXTRA_LIBRARIES
            absl::check
            absl::hash
            absl::log
            absl::log_internal_check_op
            absl::log_internal_message
            absl::strings)
    endif()
    set(${out_extra_libraries} ${MACHOME_IBKR_EXTRA_LIBRARIES} PARENT_SCOPE)
endfunction()
