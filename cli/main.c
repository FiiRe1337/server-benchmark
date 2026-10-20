/* sbench.exe – Kommandozeile des Messkerns. Vertrag: docs/cli.md */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <shellapi.h>
#include "sb/sb.h"
#include "cJSON.h"

static void usage(FILE *f) {
    fprintf(f,
        "sbench %s - portabler Windows-Server-Benchmark (Messkern)\n\n"
        "Befehle:\n"
        "  sbench run --job <job.json> [--out <result.json>] [--progress stdout|pipe:\\\\.\\pipe\\sbench-<id>|file:<pfad>]\n"
        "             [--tick-ms N] [--id <id>] [--log <pfad>] [--allow-destructive]\n"
        "  sbench stop --id <id>\n"
        "  sbench list --running --json\n"
        "  sbench info [--json]\n"
        "  sbench validate --job <job.json>\n"
        "  sbench export <result.json> --csv <out.csv> [--style iometer|flat]\n"
        "  sbench bundle --out <datei.zip> [--all] [--results <result.json>...] [--include <ordner>]...\n"
        "                [--anonymize] [--progress ...]\n"
        "  sbench version\n\n"
        "Exit-Codes: 0 ok, 1 Fehler, 2 ungültiger Job/Argumente, 3 abgebrochen, 4 fehlende Rechte\n"
        "Datenverzeichnis: %s\n",
        sb_version(), sb_data_directory());
}

static char *u8(const wchar_t *w) {
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    char *s = (char *)malloc((size_t)(n > 0 ? n : 1));
    if (s) { if (n > 0) WideCharToMultiByte(CP_UTF8, 0, w, -1, s, n, NULL, NULL); else s[0] = 0; }
    return s;
}

static const char *need(int argc, char **argv, int *i) {
    if (*i + 1 >= argc) { fprintf(stderr, "sbench: Option %s erwartet einen Wert\n", argv[*i]); return NULL; }
    return argv[++*i];
}

static int cmd_run(int argc, char **argv, const char *cmdline) {
    sb_run_options o;
    memset(&o, 0, sizeof o);
    o.cmdline = cmdline;
    for (int i = 2; i < argc; i++) {
        const char *a = argv[i];
        if (!strcmp(a, "--job")) { if (!(o.job_path = need(argc, argv, &i))) return 2; }
        else if (!strcmp(a, "--out")) { if (!(o.out_path = need(argc, argv, &i))) return 2; }
        else if (!strcmp(a, "--progress")) { if (!(o.progress = need(argc, argv, &i))) return 2; }
        else if (!strcmp(a, "--id")) { if (!(o.id = need(argc, argv, &i))) return 2; }
        else if (!strcmp(a, "--log")) { if (!(o.log_path = need(argc, argv, &i))) return 2; }
        else if (!strcmp(a, "--tick-ms")) {
            const char *v = need(argc, argv, &i);
            if (!v) return 2;
            char *end;
            long t = strtol(v, &end, 10);
            if (*end || t < 100 || t > 3600000) { fprintf(stderr, "sbench: --tick-ms muss 100..3600000 sein\n"); return 2; }
            o.tick_ms = (uint32_t)t;
        }
        else if (!strcmp(a, "--allow-destructive")) o.allow_destructive = 1;
        else { fprintf(stderr, "sbench: unbekannte Option '%s'\n", a); return 2; }
    }
    if (!o.job_path) { fprintf(stderr, "sbench run: --job fehlt\n"); return 2; }
    return sb_run(&o);
}

static int cmd_validate(int argc, char **argv) {
    const char *job = NULL;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--job")) { if (!(job = need(argc, argv, &i))) return 2; }
        else if (!job && argv[i][0] != '-') job = argv[i];
        else { fprintf(stderr, "sbench: unbekannte Option '%s'\n", argv[i]); return 2; }
    }
    if (!job) { fprintf(stderr, "sbench validate: --job fehlt\n"); return 2; }
    char *out = NULL;
    int rc = sb_validate(job, &out);
    if (out) { fputs(out, stdout); fputs("\n", stdout); sb_free(out); }
    return rc;
}

static int cmd_info(int argc, char **argv) {
    int json = 0;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--json")) json = 1;
        else { fprintf(stderr, "sbench: unbekannte Option '%s'\n", argv[i]); return 2; }
    }
    char *out = NULL;
    int rc = sb_info(&out);
    if (!out) return 1;
    if (json) { fputs(out, stdout); fputs("\n", stdout); }
    else {
        cJSON *d = cJSON_Parse(out);
        cJSON *m = cJSON_GetObjectItem(d, "meta");
        cJSON *os = cJSON_GetObjectItem(m, "os"), *cpu = cJSON_GetObjectItem(m, "cpu"), *hv = cJSON_GetObjectItem(m, "hypervisor");
        printf("Host:       %s\n", cJSON_GetStringValue(cJSON_GetObjectItem(m, "host")));
        printf("OS:         %s %s (Build %s)\n", cJSON_GetStringValue(cJSON_GetObjectItem(os, "name")),
               cJSON_GetStringValue(cJSON_GetObjectItem(os, "version")), cJSON_GetStringValue(cJSON_GetObjectItem(os, "build")));
        printf("CPU:        %s, %d Sockel, %d Kerne, %d Threads\n", cJSON_GetStringValue(cJSON_GetObjectItem(cpu, "model")),
               cJSON_GetObjectItem(cpu, "sockets")->valueint, cJSON_GetObjectItem(cpu, "cores")->valueint,
               cJSON_GetObjectItem(cpu, "threads")->valueint);
        printf("RAM:        %.1f GiB\n", cJSON_GetObjectItem(m, "ram_bytes")->valuedouble / 1073741824.0);
        printf("Hypervisor: %s, Rolle %s\n", cJSON_IsTrue(cJSON_GetObjectItem(hv, "present")) ? "ja" : "nein",
               cJSON_GetStringValue(cJSON_GetObjectItem(hv, "role")));
        printf("(vollständige Angaben mit --json)\n");
        cJSON_Delete(d);
    }
    sb_free(out);
    return rc;
}

static int cmd_export(int argc, char **argv) {
    const char *res = NULL, *csv = NULL, *style = "flat";
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--csv")) { if (!(csv = need(argc, argv, &i))) return 2; }
        else if (!strcmp(argv[i], "--style")) { if (!(style = need(argc, argv, &i))) return 2; }
        else if (!res && argv[i][0] != '-') res = argv[i];
        else { fprintf(stderr, "sbench: unbekannte Option '%s'\n", argv[i]); return 2; }
    }
    if (!res || !csv) { fprintf(stderr, "sbench export: <result.json> und --csv <out.csv> erforderlich\n"); return 2; }
    char err[700] = "";
    int rc = sb_export_csv(res, csv, style, err, sizeof err);
    if (rc) fprintf(stderr, "sbench: %s\n", err);
    return rc;
}

static int cmd_bundle(int argc, char **argv) {
    sb_bundle_options o;
    memset(&o, 0, sizeof o);
    const char **res = (const char **)calloc((size_t)argc, sizeof(char *));
    const char **inc = (const char **)calloc((size_t)argc, sizeof(char *));
    o.includes = inc;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--out")) { if (!(o.out_path = need(argc, argv, &i))) return 2; }
        else if (!strcmp(argv[i], "--progress")) { if (!(o.progress = need(argc, argv, &i))) return 2; }
        else if (!strcmp(argv[i], "--all")) o.all = 1;
        else if (!strcmp(argv[i], "--anonymize")) o.anonymize = 1;
        else if (!strcmp(argv[i], "--include")) { if (!(inc[o.nincludes++] = need(argc, argv, &i))) return 2; }
        else if (!strcmp(argv[i], "--results")) {
            int k = 0;
            while (i + 1 < argc && argv[i + 1][0] != '-') { res[o.nresults++] = argv[++i]; k++; }
            if (!k) { fprintf(stderr, "sbench: --results erwartet mindestens eine Datei oder einen Ordner\n"); return 2; }
        }
        else { fprintf(stderr, "sbench: unbekannte Option '%s'\n", argv[i]); return 2; }
    }
    if (!o.out_path) { fprintf(stderr, "sbench bundle: --out fehlt\n"); return 2; }
    o.results = res;
    char err[700] = "";
    int rc = sb_bundle(&o, err, sizeof err);
    if (rc) fprintf(stderr, "sbench: %s\n", err);
    else if (!o.progress || strcmp(o.progress, "stdout")) fprintf(stderr, "Bundle geschrieben: %s\n", o.out_path);
    free(res);
    free(inc);
    return rc;
}

static int cmd_list(int argc, char **argv) {
    int running = 0, json = 0;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--running")) running = 1;
        else if (!strcmp(argv[i], "--json")) json = 1;
        else { fprintf(stderr, "sbench: unbekannte Option '%s'\n", argv[i]); return 2; }
    }
    if (!running) { fprintf(stderr, "sbench list: --running fehlt (derzeit einzige Liste)\n"); return 2; }
    char *out = NULL;
    int rc = sb_list_running(&out);
    if (!out) return 1;
    if (json) { fputs(out, stdout); fputs("\n", stdout); }
    else {
        cJSON *d = cJSON_Parse(out), *it;
        int n = 0;
        cJSON_ArrayForEach(it, cJSON_GetObjectItem(d, "runs")) {
            printf("%s  pid %d  seit %s  -> %s\n", cJSON_GetStringValue(cJSON_GetObjectItem(it, "run_id")),
                   cJSON_GetObjectItem(it, "pid")->valueint, cJSON_GetStringValue(cJSON_GetObjectItem(it, "started_utc")),
                   cJSON_GetStringValue(cJSON_GetObjectItem(it, "out")));
            n++;
        }
        if (!n) printf("Keine laufenden Läufe.\n");
        cJSON_Delete(d);
    }
    sb_free(out);
    return rc;
}

int main(void) {
    SetConsoleOutputCP(CP_UTF8);
    int argc = 0;
    wchar_t **wargv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!wargv) return 1;
    char **argv = (char **)calloc((size_t)argc + 1, sizeof(char *));
    for (int i = 0; i < argc; i++) argv[i] = u8(wargv[i]);
    LocalFree(wargv);
    char *cmdline = u8(GetCommandLineW());
    int rc;
    if (argc < 2 || !strcmp(argv[1], "help") || !strcmp(argv[1], "--help") || !strcmp(argv[1], "-h")) {
        usage(argc < 2 ? stderr : stdout);
        rc = argc < 2 ? 2 : 0;
    } else if (!strcmp(argv[1], "run")) rc = cmd_run(argc, argv, cmdline);
    else if (!strcmp(argv[1], "stop")) {
        const char *id = NULL;
        for (int i = 2; i < argc; i++) {
            if (!strcmp(argv[i], "--id") && i + 1 < argc) id = argv[++i];
        }
        if (!id) { fprintf(stderr, "sbench stop: --id fehlt\n"); rc = 2; }
        else {
            char msg[400];
            rc = sb_stop(id, msg, sizeof msg);
            fprintf(stderr, "%s\n", msg);
        }
    }
    else if (!strcmp(argv[1], "info")) rc = cmd_info(argc, argv);
    else if (!strcmp(argv[1], "list")) rc = cmd_list(argc, argv);
    else if (!strcmp(argv[1], "validate")) rc = cmd_validate(argc, argv);
    else if (!strcmp(argv[1], "export")) rc = cmd_export(argc, argv);
    else if (!strcmp(argv[1], "bundle")) rc = cmd_bundle(argc, argv);
    else if (!strcmp(argv[1], "version") || !strcmp(argv[1], "--version")) {
        printf("sbench %s\nsb-job/1\nsb-result/1\nsb-progress/1\nsb-info/1\nsb-validate/1\nsb-runs/1\nsb-bundle/1\n", sb_version());
        rc = 0;
    } else {
        fprintf(stderr, "sbench: unbekannter Befehl '%s'\n\n", argv[1]);
        usage(stderr);
        rc = 2;
    }
    fflush(stdout);
    for (int i = 0; i < argc; i++) free(argv[i]);
    free(argv);
    free(cmdline);
    return rc;
}
