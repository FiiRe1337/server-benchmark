cmake_minimum_required(VERSION 3.20)
project(sbench VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
# Statische CRT (/MT): keine VC-Redistributable auf Zielsystemen nötig
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

if(NOT WIN32)
  message(FATAL_ERROR "sbench ist ein reines Win32-Projekt (MSVC oder MinGW-w64).")
endif()

option(SB_BUILD_TESTS "Unit-Tests bauen" ON)
set(SB_VERSION_STR "${PROJECT_VERSION}" CACHE STRING "Versionsstring des Kerns (SemVer)")

set(SB_CORE_SOURCES
  core/src/bundle.c
  core/src/export.c
  core/src/hist.c
  core/src/info.c
  core/src/job.c
  core/src/log.c
  core/src/progress.c
  core/src/rng.c
  core/src/run.c
  core/src/runs.c
  core/src/sha256.c
  core/src/storage.c
  core/src/target.c
  core/src/util.c
  third_party/cjson/cJSON.c
  third_party/miniz/miniz.c
)

# Windows 10 1607 / Server 2016 als Untergrenze
set(SB_DEFS _WIN32_WINNT=0x0A00 WINVER=0x0A00 NTDDI_VERSION=0x0A000002 WIN32_LEAN_AND_MEAN
            _CRT_SECURE_NO_WARNINGS SB_VERSION_STR="${SB_VERSION_STR}" MINIZ_NO_ZLIB_COMPATIBLE_NAMES)
set(SB_LIBS kernel32 advapi32 iphlpapi ws2_32 ole32 oleaut32 wbemuuid)

if(MSVC)
  set(SB_COPTS /W3 /utf-8 /O2 /GS /guard:cf)
  set(SB_LOPTS /guard:cf /DYNAMICBASE /NXCOMPAT)
else()
  set(SB_COPTS -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers -O2)
  set(SB_LOPTS -static -static-libgcc)
endif()

function(sb_setup tgt)
  target_include_directories(${tgt} PUBLIC core/include third_party/cjson third_party/miniz)
  target_compile_definitions(${tgt} PRIVATE ${SB_DEFS})
  target_compile_options(${tgt} PRIVATE ${SB_COPTS})
  target_link_libraries(${tgt} PUBLIC ${SB_LIBS})
endfunction()

# Statische Kernbibliothek
add_library(sbcore_static STATIC ${SB_CORE_SOURCES})
sb_setup(sbcore_static)
if(MSVC)
  set_source_files_properties(third_party/miniz/miniz.c third_party/cjson/cJSON.c PROPERTIES COMPILE_OPTIONS "/W1")
else()
  set_source_files_properties(third_party/miniz/miniz.c third_party/cjson/cJSON.c PROPERTIES COMPILE_OPTIONS "-w")
endif()

# DLL aus denselben Quellen, exportiert die API aus sb/sb.h
add_library(sbcore SHARED ${SB_CORE_SOURCES})
sb_setup(sbcore)
target_compile_definitions(sbcore PRIVATE SB_BUILD_DLL)
target_link_options(sbcore PRIVATE ${SB_LOPTS})
set_target_properties(sbcore PROPERTIES PREFIX "" OUTPUT_NAME "sbcore")

# CLI
add_executable(sbench cli/main.c)
target_link_libraries(sbench PRIVATE sbcore_static shell32)
target_compile_definitions(sbench PRIVATE ${SB_DEFS})
target_compile_options(sbench PRIVATE ${SB_COPTS})
target_link_options(sbench PRIVATE ${SB_LOPTS})

if(SB_BUILD_TESTS)
  enable_testing()
  add_executable(sb_unit_tests tests/unit/test_main.c)
  target_link_libraries(sb_unit_tests PRIVATE sbcore_static)
  target_compile_definitions(sb_unit_tests PRIVATE ${SB_DEFS})
  target_compile_options(sb_unit_tests PRIVATE ${SB_COPTS})
  target_link_options(sb_unit_tests PRIVATE ${SB_LOPTS})
  add_test(NAME unit COMMAND sb_unit_tests WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
endif()

install(TARGETS sbench sbcore RUNTIME DESTINATION . LIBRARY DESTINATION . ARCHIVE DESTINATION lib)
install(FILES core/include/sb/sb.h DESTINATION include/sb)
