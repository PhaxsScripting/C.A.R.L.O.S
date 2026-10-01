cmake_minimum_required(VERSION 3.21)
project(ev-ui VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.4 REQUIRED COMPONENTS Core Gui Qml Quick QuickControls2 Network Widgets)
# LayerShellQt keeps the HUD from stealing keyboard focus on KDE Wayland.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    option(CARLOS_LAYER_SHELL "Build native KDE Wayland overlays when available" ON)
    if(CARLOS_LAYER_SHELL)
        find_package(LayerShellQt 6.6 QUIET)
    endif()
endif()
qt_standard_project_setup()
if(Qt6_VERSION VERSION_GREATER_EQUAL 6.5)
    qt_policy(SET QTP0001 NEW)
endif()

set_source_files_properties(qml/Main.qml PROPERTIES QT_RESOURCE_ALIAS Main.qml)
set_source_files_properties(qml/CoreGraph.qml PROPERTIES QT_RESOURCE_ALIAS CoreGraph.qml)
set_source_files_properties(qml/MetricCard.qml PROPERTIES QT_RESOURCE_ALIAS MetricCard.qml)
set_source_files_properties(qml/BrainView.qml PROPERTIES QT_RESOURCE_ALIAS BrainView.qml)
set_source_files_properties(qml/BrainExplorer.qml PROPERTIES QT_RESOURCE_ALIAS BrainExplorer.qml)
set_source_files_properties(qml/DailyView.qml PROPERTIES QT_RESOURCE_ALIAS DailyView.qml)
foreach(component HudPanel SciButton SciField HudBackdrop NeuralHologram AgentOrb ConfirmationPrompt ListScrollBar MemoryView)
    set_source_files_properties(qml/${component}.qml PROPERTIES QT_RESOURCE_ALIAS ${component}.qml)
endforeach()
set_source_files_properties(assets/ev-neural-brain.png PROPERTIES QT_RESOURCE_ALIAS assets/ev-neural-brain.png)

qt_add_executable(ev-ui
    src/main.cpp
    src/EvClient.cpp
    src/EvClient.h
)

set(EV_HUD_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/qml/HudWindow.qml")
if(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
    configure_file(platform/FreeBSDHudWindow.qml "${CMAKE_CURRENT_BINARY_DIR}/HudWindow.qml" COPYONLY)
    set(EV_HUD_SOURCE "${CMAKE_CURRENT_BINARY_DIR}/HudWindow.qml")
endif()
set_source_files_properties(${EV_HUD_SOURCE} PROPERTIES QT_RESOURCE_ALIAS HudWindow.qml)
qt_add_qml_module(ev-ui
    URI EV.ControlCenter
    VERSION 1.0
    RESOURCE_PREFIX /qt/qml
    QML_FILES
        ${EV_HUD_SOURCE}
        qml/Main.qml
        qml/MemoryView.qml
        qml/ConfirmationPrompt.qml
        qml/ListScrollBar.qml
        qml/CoreGraph.qml
        qml/MetricCard.qml
        qml/BrainView.qml
        qml/BrainExplorer.qml
        qml/DailyView.qml
        qml/HudPanel.qml
        qml/SciButton.qml
        qml/SciField.qml
        qml/HudBackdrop.qml
        qml/NeuralHologram.qml
        qml/AgentOrb.qml
    RESOURCES
        assets/ev-neural-brain.png
)

target_link_libraries(ev-ui PRIVATE
    Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick Qt6::QuickControls2 Qt6::Network Qt6::Widgets
)

install(TARGETS ev-ui RUNTIME DESTINATION bin)

include(CTest)
if(BUILD_TESTING)
    find_package(Qt6 REQUIRED COMPONENTS Test)
    qt_add_executable(ev-client-tests ../tests/test_ev_client.cpp src/EvClient.cpp src/EvClient.h)
    target_include_directories(ev-client-tests PRIVATE src)
    target_link_libraries(ev-client-tests PRIVATE Qt6::Core Qt6::Network Qt6::Test)
    add_test(NAME ev-client-tests COMMAND ev-client-tests)
    qt_add_executable(carlos-window-bridge-tests ../tests/test_window_bridge.cpp)
    target_link_libraries(carlos-window-bridge-tests PRIVATE Qt6::Core Qt6::Qml Qt6::Test)
    target_compile_definitions(carlos-window-bridge-tests PRIVATE CARLOS_WINDOW_BRIDGE="${CMAKE_CURRENT_SOURCE_DIR}/../assets/kwin/bridge.js")
    add_test(NAME carlos-window-bridge COMMAND carlos-window-bridge-tests)
    find_program(EV_QML_TEST_RUNNER qmltestrunner HINTS /usr/lib64/qt6/bin /usr/lib/qt6/bin /usr/local/lib/qt6/bin)
    if(EV_QML_TEST_RUNNER)
        add_test(NAME ev-interface-tests COMMAND ${EV_QML_TEST_RUNNER} -input ${CMAKE_CURRENT_SOURCE_DIR}/../tests/qml -platform offscreen)
        set_tests_properties(ev-interface-tests PROPERTIES ENVIRONMENT "QT_QUICK_BACKEND=software;QT_QUICK_CONTROLS_STYLE=Basic" TIMEOUT 45)
    endif()
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    find_package(Qt6 REQUIRED COMPONENTS DBus)
    qt_add_executable(ev-pet pet/main.cpp pet/PetController.cpp pet/PetController.h)
    set_source_files_properties(pet/Pet.qml PROPERTIES QT_RESOURCE_ALIAS Pet.qml)
    set_source_files_properties(pet/PetSprite.qml PROPERTIES QT_RESOURCE_ALIAS PetSprite.qml)
    qt_add_qml_module(ev-pet URI Carlos.Pet VERSION 1.0 RESOURCE_PREFIX /qt/qml QML_FILES pet/Pet.qml pet/PetSprite.qml)
    qt_add_resources(ev-pet pet-scripts PREFIX "/pet" BASE pet FILES pet/activity.js pet/drag.js)
    target_link_libraries(ev-pet PRIVATE Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick Qt6::Widgets Qt6::Network Qt6::DBus)
    install(TARGETS ev-pet RUNTIME DESTINATION bin)
    if(BUILD_TESTING)
        add_executable(carlos-pet-tests ../tests/pet/test_pet.cpp)
        target_include_directories(carlos-pet-tests PRIVATE pet)
        target_link_libraries(carlos-pet-tests PRIVATE Qt6::Core Qt6::Test)
        add_test(NAME carlos-pet-policy COMMAND carlos-pet-tests)
        qt_add_executable(carlos-pet-controller-tests ../tests/pet/test_controller.cpp pet/PetController.cpp pet/PetController.h)
        target_include_directories(carlos-pet-controller-tests PRIVATE pet)
        target_link_libraries(carlos-pet-controller-tests PRIVATE Qt6::Core Qt6::Gui Qt6::Quick Qt6::Widgets Qt6::Network Qt6::DBus Qt6::Test)
        add_test(NAME carlos-pet-controller COMMAND carlos-pet-controller-tests)
        set_tests_properties(carlos-pet-controller PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
    endif()
endif()

if(TARGET LayerShellQt::Interface AND CARLOS_LAYER_SHELL)
    foreach(target ev-ui ev-pet carlos-pet-controller-tests)
        if(TARGET ${target})
            target_compile_definitions(${target} PRIVATE CARLOS_HAVE_LAYER_SHELL=1)
            target_link_libraries(${target} PRIVATE LayerShellQt::Interface)
        endif()
    endforeach()
endif()
