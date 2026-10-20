CMAKE_MINIMUM_REQUIRED(VERSION 2.8)

include(RezBuild)

file(GLOB_RECURSE bin_files "bin/*")
rez_install_files(
    ${bin_files}
    DESTINATION .
    EXECUTABLE
)

file(REMOVE_RECURSE "python/*.pyc")

file(GLOB_RECURSE py_files "python/*.py")
rez_install_python(
    py
    FILES ${py_files}
    DESTINATION .
)

# Non-Python resources (SGI theme fonts, icons): rez_install_python only takes .py
file(GLOB_RECURSE res_files
    "python/ilp_gallery/ui/icons/*"
    "python/ilp_gallery/ui/sgi/fonts/*"
)
rez_install_files(
    ${res_files}
    DESTINATION .
)

#file(REMOVE_RECURSE "houdini/otls/backup")
#file(REMOVE_RECURSE "houdini/hip/backup")
#file(GLOB_RECURSE houdini_files "houdini/*")
#rez_install_files(
    #${houdini_files}
    #DESTINATION .
#)
