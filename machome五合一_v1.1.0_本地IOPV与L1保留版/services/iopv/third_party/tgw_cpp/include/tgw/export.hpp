#pragma once

#if defined(_WIN32) && defined(TGW_CPP_SHARED)
#  if defined(TGW_CPP_BUILDING)
#    define TGW_API __declspec(dllexport)
#  else
#    define TGW_API __declspec(dllimport)
#  endif
#elif defined(__GNUC__) || defined(__clang__)
#  define TGW_API __attribute__((visibility("default")))
#else
#  define TGW_API
#endif

