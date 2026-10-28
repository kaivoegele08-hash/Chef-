/* Test-"Spiel": legt beliebige benannte Mappings an und spielt Datei-Schnappschüsse ab.
 *   fake_map_game.exe SEKUNDEN NAME=datei1[,datei2,...] [NAME=...]
 * Alle 20 ms wird je Mapping der nächste Schnappschuss hineinkopiert (zyklisch). */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXM 8
#define MAXF 8

typedef struct { char *data[MAXF]; long size[MAXF]; int n; char *view; long view_size; } M;

static char *load(const char *path, long *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "open %s failed\n", path); exit(1); }
    fseek(f, 0, SEEK_END); *size = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc(*size);
    if (fread(buf, 1, *size, f) != (size_t)*size) { fprintf(stderr, "read %s failed\n", path); exit(1); }
    fclose(f);
    return buf;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: fake_map_game SECONDS NAME=file[,file]...\n"); return 2; }
    int seconds = atoi(argv[1]);
    M maps[MAXM];
    int nm = 0;
    for (int a = 2; a < argc && nm < MAXM; a++) {
        char *spec = strdup(argv[a]);
        char *eq = strchr(spec, '=');
        if (!eq) continue;
        *eq = 0;
        M *m = &maps[nm];
        memset(m, 0, sizeof *m);
        for (char *tok = strtok(eq + 1, ","); tok && m->n < MAXF; tok = strtok(NULL, ",")) {
            m->data[m->n] = load(tok, &m->size[m->n]);
            if (m->size[m->n] > m->view_size) m->view_size = m->size[m->n];
            m->n++;
        }
        HANDLE h = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, (DWORD)m->view_size, spec);
        if (!h) { fprintf(stderr, "CreateFileMapping %s failed\n", spec); return 1; }
        m->view = MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, m->view_size);
        nm++;
    }
    for (int i = 0; i < seconds * 50; i++) {
        for (int k = 0; k < nm; k++) {
            M *m = &maps[k];
            int j = i % m->n;
            memcpy(m->view, m->data[j], m->size[j]);
        }
        Sleep(20);
    }
    return 0;
}
