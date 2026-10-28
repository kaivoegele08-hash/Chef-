/*
 * pitwall-relay – liest Windows-Shared-Memory von Rennsimulationen und schickt
 * es per UDP an PIT WALL (127.0.0.1). Gedacht für Linux/Proton: Der Relay läuft
 * im selben Proton-Prefix wie das Spiel (Steam-Startoption, siehe
 * docs/linux-proton.md), PIT WALL läuft nativ unter Linux.
 *
 * Protokoll "PWR1" (Little Endian):
 *   Hello  (alle 2 s):  "PWRH" + Text  "id|name|size|open\n" je Map
 *   Daten:              Header (20 Byte) + n × (uint32 page_index + Seite)
 *       char  magic[4] = "PWR1"
 *       u16   map_id
 *       u16   flags      1 = Beginn Snapshot, 2 = Ende Snapshot, 4 = Vollbild
 *       u32   seq        Snapshot-Zähler je Map
 *       u32   map_size
 *       u16   page_count
 *       u16   reserved
 *   Seiten = 4096 Byte (letzte Seite ggf. kürzer). Es werden nur geänderte
 *   Seiten gesendet, alle 2 s ein Vollbild zur Resynchronisation.
 *
 * Bauen (Linux, mingw):  x86_64-w64-mingw32-gcc -O2 -s -o pitwall-relay.exe pitwall_relay.c -lws2_32 -lwinmm
 * Lizenz: MIT (Teil von PIT WALL)
 */
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <mmsystem.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define PAGE 4096
#define MAX_MAPS 16
#define MAX_DGRAM 61440
#define VERSION "1.0"

typedef struct {
    char name[128];
    size_t size;        /* 0 = per VirtualQuery ermitteln */
    int hz;             /* Abtastrate dieser Map */
    HANDLE handle;
    const uint8_t *view;
    size_t view_size;
    uint8_t *prev;
    uint32_t seq;
    ULONGLONG next_due;
    ULONGLONG next_full;
    ULONGLONG next_open_try;
    ULONGLONG last_change;
} Map;

static Map maps[MAX_MAPS];
static int map_count = 0;
static SOCKET sock = INVALID_SOCKET;
static struct sockaddr_in dest;
static int verbose = 0;
static int quit_after_s = 0; /* 0 = nie; sonst beenden, wenn so lange keine Map offen war */

static void add_map(const char *spec, int default_hz) {
    if (map_count >= MAX_MAPS) return;
    Map *m = &maps[map_count++];
    memset(m, 0, sizeof(*m));
    m->hz = default_hz;
    /* Format: NAME[:SIZE[:HZ]] */
    char buf[256];
    strncpy(buf, spec, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = 0;
    char *p1 = strchr(buf, ':');
    if (p1) {
        *p1++ = 0;
        char *p2 = strchr(p1, ':');
        if (p2) { *p2++ = 0; m->hz = atoi(p2); }
        m->size = (size_t)strtoull(p1, NULL, 10);
    }
    snprintf(m->name, sizeof(m->name), "%s", buf);
    if (m->hz <= 0) m->hz = default_hz;
}

static void preset(const char *sim, int hz) {
    if (!strcmp(sim, "ac") || !strcmp(sim, "acc") || !strcmp(sim, "assetto_corsa")) {
        char a[64];
        snprintf(a, sizeof a, "Local\\acpmf_physics:0:%d", hz); add_map(a, hz);
        snprintf(a, sizeof a, "Local\\acpmf_graphics:0:%d", hz); add_map(a, hz);
        add_map("Local\\acpmf_static:0:2", 2);
    } else if (!strcmp(sim, "iracing")) {
        char a[64];
        snprintf(a, sizeof a, "Local\\IRSDKMemMapFileName:0:%d", hz); add_map(a, hz);
    } else if (!strcmp(sim, "rf2") || !strcmp(sim, "rfactor2")) {
        char a[64];
        snprintf(a, sizeof a, "$rFactor2SMMP_Telemetry$:0:%d", hz); add_map(a, hz);
        add_map("$rFactor2SMMP_Scoring$:0:10", 10);
        add_map("$rFactor2SMMP_Rules$:0:5", 5);
        add_map("$rFactor2SMMP_Extended$:0:2", 2);
    } else if (!strcmp(sim, "lmu")) {
        char a[64];
        snprintf(a, sizeof a, "LMU_Data:0:%d", hz); add_map(a, hz);
        snprintf(a, sizeof a, "$rFactor2SMMP_Telemetry$:0:%d", hz); add_map(a, hz);
        add_map("$rFactor2SMMP_Scoring$:0:10", 10);
    } else if (!strcmp(sim, "ets2") || !strcmp(sim, "ats")) {
        add_map("Local\\SCSTelemetry:0:20", 20);
    } else {
        fprintf(stderr, "pitwall-relay: unbekanntes --sim %s\n", sim);
    }
}

static void close_map(Map *m) {
    if (m->view) UnmapViewOfFile(m->view);
    if (m->handle) CloseHandle(m->handle);
    m->view = NULL;
    m->handle = NULL;
    free(m->prev);
    m->prev = NULL;
    m->view_size = 0;
}

static int try_open(Map *m) {
    HANDLE h = OpenFileMappingA(FILE_MAP_READ, FALSE, m->name);
    if (!h) return 0;
    const uint8_t *v = (const uint8_t *)MapViewOfFile(h, FILE_MAP_READ, 0, 0, m->size);
    if (!v) { CloseHandle(h); return 0; }
    size_t size = m->size;
    if (size == 0) {
        MEMORY_BASIC_INFORMATION mbi;
        if (VirtualQuery(v, &mbi, sizeof mbi) == 0) { UnmapViewOfFile(v); CloseHandle(h); return 0; }
        size = mbi.RegionSize;
    }
    m->handle = h;
    m->view = v;
    m->view_size = size;
    m->prev = (uint8_t *)calloc(1, size);
    m->next_full = 0;
    m->last_change = GetTickCount64();
    if (verbose) fprintf(stderr, "pitwall-relay: %s geöffnet (%zu Bytes)\n", m->name, size);
    return 1;
}

static void send_hello(void) {
    char buf[4096];
    int n = snprintf(buf, sizeof buf, "PWRH");
    for (int i = 0; i < map_count && n < (int)sizeof(buf) - 200; i++) {
        n += snprintf(buf + n, sizeof(buf) - n, "%d|%s|%zu|%d\n", i, maps[i].name, maps[i].view_size,
                      maps[i].view != NULL);
    }
    n += snprintf(buf + n, sizeof(buf) - n, "version|%s\n", VERSION);
    sendto(sock, buf, n, 0, (struct sockaddr *)&dest, sizeof dest);
}

#pragma pack(push, 1)
typedef struct {
    char magic[4];
    uint16_t map_id;
    uint16_t flags;
    uint32_t seq;
    uint32_t map_size;
    uint16_t page_count;
    uint16_t reserved;
} Header;
#pragma pack(pop)

static uint8_t dgram[MAX_DGRAM + 64];

static void send_snapshot(int id, Map *m, int full) {
    size_t pages = (m->view_size + PAGE - 1) / PAGE;
    uint8_t *cur = (uint8_t *)malloc(m->view_size);
    if (!cur) return;
    /* Schnappschuss kopieren (kurz, damit das Spiel kaum dazwischen schreibt) */
    memcpy(cur, m->view, m->view_size);
    m->seq++;
    size_t per_dgram = (MAX_DGRAM - sizeof(Header)) / (PAGE + 4);
    size_t pending_idx[64];
    size_t pending = 0;
    int first = 1;
    for (size_t p = 0; p <= pages; p++) {
        int flush = 0;
        if (p < pages) {
            size_t off = p * PAGE;
            size_t len = (off + PAGE <= m->view_size) ? PAGE : m->view_size - off;
            int changed = memcmp(cur + off, m->prev + off, len) != 0;
            if (changed) m->last_change = GetTickCount64();
            if (full || changed) {
                pending_idx[pending++] = p;
                if (pending >= per_dgram) flush = 1;
            }
        } else {
            flush = 1; /* Ende: Rest + End-Flag senden (auch wenn leer) */
        }
        if (!flush) continue;
        Header h;
        memcpy(h.magic, "PWR1", 4);
        h.map_id = (uint16_t)id;
        h.flags = (uint16_t)((first ? 1 : 0) | (p >= pages ? 2 : 0) | (full ? 4 : 0));
        h.seq = m->seq;
        h.map_size = (uint32_t)m->view_size;
        h.page_count = (uint16_t)pending;
        h.reserved = 0;
        size_t n = 0;
        memcpy(dgram, &h, sizeof h);
        n += sizeof h;
        for (size_t k = 0; k < pending; k++) {
            uint32_t idx = (uint32_t)pending_idx[k];
            size_t off = (size_t)idx * PAGE;
            size_t len = (off + PAGE <= m->view_size) ? PAGE : m->view_size - off;
            memcpy(dgram + n, &idx, 4);
            n += 4;
            memcpy(dgram + n, cur + off, len);
            n += len;
        }
        sendto(sock, (const char *)dgram, (int)n, 0, (struct sockaddr *)&dest, sizeof dest);
        first = 0;
        pending = 0;
    }
    memcpy(m->prev, cur, m->view_size);
    free(cur);
}

static void usage(void) {
    fprintf(stderr,
            "pitwall-relay " VERSION " – Shared Memory → UDP für PIT WALL\n"
            "  --sim ac|acc|iracing|rf2|lmu|ets2   Preset der Maps\n"
            "  --map NAME[:SIZE[:HZ]]             zusätzliche Map (mehrfach)\n"
            "  --port 20790  --host 127.0.0.1  --hz 60\n"
            "  --quit-after SEKUNDEN               beenden, wenn so lange keine Map offen war\n"
            "  --verbose\n");
}

int main(int argc, char **argv) {
    const char *host = "127.0.0.1";
    int port = 20790;
    int hz = 60;
    const char *sims[8];
    int nsims = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--port") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--host") && i + 1 < argc) host = argv[++i];
        else if (!strcmp(argv[i], "--hz") && i + 1 < argc) hz = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--sim") && i + 1 < argc && nsims < 8) sims[nsims++] = argv[++i];
        else if (!strcmp(argv[i], "--map") && i + 1 < argc) add_map(argv[++i], hz);
        else if (!strcmp(argv[i], "--quit-after") && i + 1 < argc) quit_after_s = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--verbose")) verbose = 1;
        else if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) { usage(); return 0; }
        else if (!strcmp(argv[i], "--appid") && i + 1 < argc) i++; /* nur Info */
    }
    if (hz < 1) hz = 1;
    if (hz > 360) hz = 360;
    for (int i = 0; i < nsims; i++) preset(sims[i], hz);
    if (map_count == 0) {
        /* ohne Angabe: alle bekannten Sims beobachten */
        preset("ac", hz); preset("iracing", hz); preset("rf2", hz); preset("lmu", hz);
    }
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) { fprintf(stderr, "WSAStartup fehlgeschlagen\n"); return 1; }
    sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock == INVALID_SOCKET) { fprintf(stderr, "socket fehlgeschlagen\n"); return 1; }
    int sndbuf = 4 * 1024 * 1024;
    setsockopt(sock, SOL_SOCKET, SO_SNDBUF, (const char *)&sndbuf, sizeof sndbuf);
    memset(&dest, 0, sizeof dest);
    dest.sin_family = AF_INET;
    dest.sin_port = htons((u_short)port);
    inet_pton(AF_INET, host, &dest.sin_addr);
    timeBeginPeriod(1);
    fprintf(stderr, "pitwall-relay " VERSION ": %d Maps → %s:%d\n", map_count, host, port);

    ULONGLONG next_hello = 0;
    ULONGLONG last_open = GetTickCount64();
    for (;;) {
        ULONGLONG now = GetTickCount64();
        int any_open = 0;
        for (int i = 0; i < map_count; i++) {
            Map *m = &maps[i];
            if (!m->view) {
                if (now >= m->next_open_try) {
                    m->next_open_try = now + 1000;
                    try_open(m);
                }
                continue;
            }
            any_open = 1;
            if (now >= m->next_due) {
                m->next_due = now + (ULONGLONG)(1000 / m->hz);
                int full = now >= m->next_full;
                if (full) m->next_full = now + 2000;
                send_snapshot(i, m, full);
            }
        }
        if (any_open) last_open = now;
        if (now >= next_hello) {
            next_hello = now + 2000;
            send_hello();
            /* Unser eigenes Handle hält die Maps am Leben. Ändert sich in KEINER Map 5 s lang etwas
               (Spiel beendet), alle schließen und neu öffnen – existiert das Spiel nicht mehr, schlägt das
               Öffnen fehl. (Statische Maps ändern sich nie und werden daher nicht einzeln geprüft.) */
            ULONGLONG newest = 0;
            int open_maps = 0;
            for (int i = 0; i < map_count; i++) {
                if (maps[i].view) {
                    open_maps++;
                    if (maps[i].last_change > newest) newest = maps[i].last_change;
                }
            }
            if (open_maps && now - newest > 5000) {
                for (int i = 0; i < map_count; i++) {
                    if (maps[i].view) {
                        close_map(&maps[i]);
                        maps[i].next_open_try = now + 500;
                    }
                }
            }
        }
        if (quit_after_s > 0 && now - last_open > (ULONGLONG)quit_after_s * 1000) {
            fprintf(stderr, "pitwall-relay: %d s keine Daten – beende\n", quit_after_s);
            break;
        }
        Sleep(1);
    }
    for (int i = 0; i < map_count; i++) close_map(&maps[i]);
    closesocket(sock);
    WSACleanup();
    timeEndPeriod(1);
    return 0;
}
