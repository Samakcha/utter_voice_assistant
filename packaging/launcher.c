/*
 * Utter.app launcher: the app's main executable (Contents/MacOS/Utter).
 *
 * It runs Python *inside this process* (embedded), instead of starting a separate python
 * program, so macOS sees one app called Utter: the Microphone, Accessibility, Input
 * Monitoring and Automation permissions are asked for, and remembered, as "Utter".
 *
 * Bundle layout it expects:
 *   Contents/Resources/python/          a private copy of Python (lib/libpython3.12.dylib,
 *                                       lib/python3.12 with site-packages inside)
 *   Contents/Resources/app/main.py      Utter itself (engine/, interface/, config.toml, ...)
 *
 * Protected: no PYTHON* environment variables, no user site-packages, and no .pyc
 * files written at run time (they are compiled at build time; writing into the signed app
 * would break its signature).
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void fail(const char *what, PyStatus status, PyConfig *config) {
    fprintf(stderr, "Utter could not start Python (%s): %s\n", what,
            status.err_msg ? status.err_msg : "unknown error");
    if (config) PyConfig_Clear(config);
    exit(1);
}

int main(int argc, char *argv[]) {
    char exe[PATH_MAX], real[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, real) == NULL) {
        fprintf(stderr, "Utter could not find its own location.\n");
        return 1;
    }
    /* real = <bundle>/Contents/MacOS/Utter  ->  contents = <bundle>/Contents */
    char contents[PATH_MAX];
    strncpy(contents, real, sizeof(contents) - 1);
    contents[sizeof(contents) - 1] = '\0';
    for (int i = 0; i < 2; i++) {
        char *slash = strrchr(contents, '/');
        if (slash) *slash = '\0';
    }

    char home[PATH_MAX], script[PATH_MAX];
    snprintf(home, sizeof(home), "%s/Resources/python", contents);
    snprintf(script, sizeof(script), "%s/Resources/app/main.py", contents);

    PyStatus status;

    /* Apps started by macOS have no LANG, which would make Python's text ASCII-only:
       UTF-8 mode makes all text (logs, file names, "✓") UTF-8 regardless. */
    PyPreConfig preconfig;
    PyPreConfig_InitIsolatedConfig(&preconfig);
    preconfig.utf8_mode = 1;
    status = Py_PreInitialize(&preconfig);
    if (PyStatus_Exception(status)) fail("text encoding", status, NULL);

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    /* Isolated, except that main.py's folder (Utter's code) goes on sys.path: full isolated
       mode would force safe_path back on. The other protections stay. */
    config.isolated = 0;
    config.safe_path = 0;
    config.use_environment = 0;      /* ignore PYTHONPATH, PYTHONHOME, ... */
    config.user_site_directory = 0;  /* ignore ~/.local packages */
    config.write_bytecode = 0;
    config.parse_argv = 1;

    status = PyConfig_SetBytesString(&config, &config.home, home);
    if (PyStatus_Exception(status)) fail("home", status, &config);
    status = PyConfig_SetBytesString(&config, &config.program_name, real);
    if (PyStatus_Exception(status)) fail("program name", status, &config);

    /* Libraries start helper processes by running this program as if it were Python itself,
       e.g. multiprocessing's resource tracker: `Utter -B -c "from multiprocessing..."`.
       Python's own options start with a single "-" (Utter's are "--check", "--self-test"),
       so then behave as plain Python instead of starting a second Utter. */
    int python_mode = argc > 1 && argv[1][0] == '-' && argv[1][1] != '-' && argv[1][1] != '\0';

    /* Otherwise the same as running: python main.py <arguments given to the app> */
    char **args = calloc((size_t)argc + 2, sizeof(char *));
    if (!args) return 1;
    int n = 0;
    args[n++] = real;
    if (!python_mode) args[n++] = script;
    for (int i = 1; i < argc; i++) args[n++] = argv[i];
    status = PyConfig_SetBytesArgv(&config, n, args);
    free(args);
    if (PyStatus_Exception(status)) fail("arguments", status, &config);

    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) fail("startup", status, NULL);
    return Py_RunMain();
}
